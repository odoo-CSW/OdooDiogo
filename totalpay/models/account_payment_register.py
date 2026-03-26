import logging
import re
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from . import constants

_logger = logging.getLogger(__name__)

class AccountPaymentRegister(models.TransientModel):
    _inherit = ['account.payment.register', 'payment.method.mixin']
    _name = 'account.payment.register'
    
    # Nota: A lógica de preenchimento automático de telemóvel e email
    # está implementada no payment.method.mixin via _onchange_partner_payment_method

    def _create_payment_vals_from_wizard(self, batch_result):
        """
        Sobrescreve para adicionar x_mbway_phone e x_paypal_email
        aos valores de criação do pagamento
        """
        payment_vals = super()._create_payment_vals_from_wizard(batch_result)
        
        # Adicionar telefone MB WAY aos valores de criação
        if self.x_mbway_phone:
            payment_vals['x_mbway_phone'] = self.x_mbway_phone
            _logger.info("[WIZARD→VALS] Adicionando x_mbway_phone aos valores de criação: %s", self.x_mbway_phone)
        
        # Adicionar email PayPal aos valores de criação
        if self.x_paypal_email:
            payment_vals['x_paypal_email'] = self.x_paypal_email
            _logger.info("[WIZARD→VALS] Adicionando x_paypal_email aos valores de criação: %s", self.x_paypal_email)
        
        return payment_vals

    def action_create_payments(self):
        # Se defer_totalpay_processing está ativo, deixar ação de servidor gerir popups
        if self.env.context.get('defer_totalpay_processing'):
            return super(AccountPaymentRegister, self).action_create_payments()
        
        # Verificar se é método Total Pay
        if not self.payment_method_line_id or not self.payment_method_line_id.payment_method_id:
            return super(AccountPaymentRegister, self).action_create_payments()
        
        method_code = self.payment_method_line_id.payment_method_id.code.upper()
        
        if method_code not in [constants.PAYMENT_METHOD_MBWAY, constants.PAYMENT_METHOD_MULTIBANCO]:
            return super(AccountPaymentRegister, self).action_create_payments()

        if self.amount <= 0:
            raise UserError(_('Não é possível processar pagamentos TotalPay com valor inválido: %s') % "{:.2f}€".format(float(self.amount)))

        # VERIFICAR LICENÇA APENAS UMA VEZ (para todos os pagamentos)
        license_action = self.env['account.payment']._ensure_totalpay_license_active()
        if isinstance(license_action, dict):
            return license_action
        
        # 1. CRIAR PAGAMENTO(S) COM CONTEXTO PARA EVITAR DUPLICAÇÃO E VERIFICAÇÕES EXTRAS
        result = super(AccountPaymentRegister, self.with_context(
            from_payment_register_wizard=True,
            totalpay_skip_auto=True,
            totalpay_license_checked=True  # Já verificamos a licença acima
        )).action_create_payments()
        
        if result is True:
             result = {'type': 'ir.actions.act_window_close'}
        # ----------------------------------------------------
        
        # Buscar pagamentos criados
        payments = self._get_created_payments(result)
        
        if not payments:
            _logger.error("Pagamentos não encontrados após criação")
            return result

        # CONFIRMAR os pagamentos para gerar o número final (name)
        try:
            payments.with_context(totalpay_skip_auto=True).action_post()
        except UserError:
            raise
        except Exception as e:
            _logger.error("[TOTALPAY] Erro ao confirmar pagamentos: %s", str(e))
            return result
        
        # Refresh dos pagamentos para garantir que temos o número correto
        payments.invalidate_recordset(['name'])
        payments = payments.exists()

        # Fluxo single: usar apenas o primeiro pagamento retornado
        payment = payments[:1]
        if not payment:
            return result
        payment = payment[0]
        
        # 2. CRIAR CONNECTOR (Imediato)
        connector = self._create_connector_for_payment(payment, method_code, is_batch=False)
        
        if not connector:
            _logger.error("Connector não criado para pagamento %s", payment.name)
            return result
        
        # 3. FAZER PEDIDO À API
        try:
            api_result = connector.action_create_payment_request()
            connector.invalidate_recordset()
            connector = self.env['x_csw_totalpay'].browse(connector.id)
        except Exception as e:
            _logger.error("Erro ao processar API para %s: %s", payment.name, str(e))
            return result
        
        # 4. POPUP VIA BUS (frontend decide qual popup abrir)
        if connector.x_studio_stage_id.id == constants.STAGE_FALHOU:
            backend_error = connector.x_studio_error_message or ''
            notify_message = ''
            if isinstance(api_result, dict):
                notify_message = (api_result.get('params', {}) or {}).get('message', '')

            if backend_error:
                _logger.error("[TOTALPAY] Falha %s: %s", method_code, backend_error)

            method_label = 'MB WAY' if method_code == constants.PAYMENT_METHOD_MBWAY else 'MULTIBANCO'
            error_msg = backend_error or notify_message or _(
                'Não foi possível concluir o pagamento %s. Por favor, tente novamente.'
            ) % method_label

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': f'❌ Erro {method_code}',
                    'message': error_msg,
                    'type': 'danger',
                    'sticky': False,
                }
            }

        payment._notify_totalpay_popup([payment.id])
        
        # Se não há popup para mostrar, retorna o resultado original
        if not isinstance(result, dict):
             return {'type': 'ir.actions.act_window_close'}
             
        return result

    def _get_created_payments(self, result):
        """
        Busca TODOS os pagamentos criados a partir do resultado de action_create_payments.
        Retorna um recordset de account.payment.
        """
        payments = self.env['account.payment']
        
        # Caso 1: Retorno 'res_id' (Form View / Single)
        if isinstance(result, dict) and 'res_id' in result:
            payments = self.env['account.payment'].browse(result['res_id'])
            
        # Caso 2: Retorno 'domain' (List View / Multiple)
        elif isinstance(result, dict) and 'domain' in result:
            payments = self.env['account.payment'].search(result['domain'])
            
        # Caso 3: Fallback - buscar últimos criados pelo user nesta sessão
        if not payments:
            time_threshold = fields.Datetime.now() - timedelta(seconds=5)
            payments = self.env['account.payment'].search([
                ('create_uid', '=', self.env.uid),
                ('create_date', '>=', time_threshold)
            ], order='create_date desc')
        
        payments = payments.exists()
        return payments
    
    def _create_connector_for_payment(self, payment, method_code, is_batch=False):
        """Cria connector para o pagamento usando método centralizado"""
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', payment.id)
        ], limit=1)
        
        if connector:
            if is_batch and not connector.x_waiting_batch_process and connector.x_studio_stage_id.id == constants.STAGE_PENDENTE:
                connector.write({'x_waiting_batch_process': True})
            return connector
        
        config = self.env['x_csw_totalpay_config'].sudo().search([], limit=1)
        if not config:
            raise UserError("Configure o módulo Total Pay antes de criar pagamentos.")
        
        # Usar método centralizado de account.payment para preparar valores
        values = payment._prepare_connector_values(method_code, config, is_batch)
        
        try:
            connector = self.env['x_csw_totalpay'].with_context(from_api=True).create(values)
            
            if not connector.account_payment_id or connector.account_payment_id.id != payment.id:
                _logger.error("Connector criado mas account_payment_id incorreto: esperado %s, obtido %s", 
                              payment.id, connector.account_payment_id.id if connector.account_payment_id else None)
            
            return connector
        except Exception as e:
            _logger.error("Erro ao criar connector para %s: %s", payment.name, str(e))
            raise UserError(f"Erro ao processar pagamento {payment.name}. Verifique a configuração TotalPay.")


def post_init_hook(env):
    """
    Hook executado após a instalação do módulo.
    Cria os métodos de pagamento MB Way, PayPal e MULTIBANCO e adiciona aos diários bancários.
    """
    _logger.info("=== Executando post_init_hook do csw_totalpay ===")
    
    try:
        PaymentMethod = env['account.payment.method']
        
        # Criar método MB Way
        mbway_method = PaymentMethod.search([
            ('code', '=', 'MBWAY'),
            ('payment_type', '=', 'inbound')
        ], limit=1)
        
        if not mbway_method:
            mbway_method = PaymentMethod.create({
                'name': 'MB Way',
                'code': 'MBWAY',
                'payment_type': 'inbound',
            })
            _logger.info("✅ Método MB Way criado (ID: %s)", mbway_method.id)
        else:
            _logger.info("ℹ️  Método MB Way já existe (ID: %s)", mbway_method.id)
        
        # Criar método PayPal
        paypal_method = PaymentMethod.search([
            ('code', '=', 'paypal'),
            ('payment_type', '=', 'inbound')
        ], limit=1)
        
        if not paypal_method:
            paypal_method = PaymentMethod.create({
                'name': 'PayPal',
                'code': 'paypal',
                'payment_type': 'inbound',
            })
            _logger.info("✅ Método PayPal criado (ID: %s)", paypal_method.id)
        else:
            _logger.info("ℹ️  Método PayPal já existe (ID: %s)", paypal_method.id)
        
        # Criar método MULTIBANCO
        multibanco_method = PaymentMethod.search([
            ('code', '=', 'MULTIBANCO'),
            ('payment_type', '=', 'inbound')
        ], limit=1)
        
        if not multibanco_method:
            multibanco_method = PaymentMethod.create({
                'name': 'Multibanco',
                'code': 'MULTIBANCO',
                'payment_type': 'inbound',
            })
            _logger.info("✅ Método MULTIBANCO criado (ID: %s)", multibanco_method.id)
        else:
            _logger.info("ℹ️  Método MULTIBANCO já existe (ID: %s)", multibanco_method.id)
        
        # Fazer commit
        env.cr.commit()
        
        # Verificar se foram realmente criados
        check_mbway = PaymentMethod.search([('code', '=', 'MBWAY'), ('payment_type', '=', 'inbound')])
        check_paypal = PaymentMethod.search([('code', '=', 'paypal'), ('payment_type', '=', 'inbound')])
        check_multibanco = PaymentMethod.search([('code', '=', 'MULTIBANCO'), ('payment_type', '=', 'inbound')])
        _logger.info("🔍 Verificação: MB Way existe? %s | PayPal existe? %s | MULTIBANCO existe? %s", 
                     bool(check_mbway), bool(check_paypal), bool(check_multibanco))
        
        # Adicionar aos diários bancários
        _add_methods_to_journals(env, mbway_method, paypal_method, multibanco_method)
        
    except Exception as e:
        _logger.error("❌ Erro no post_init_hook: %s", str(e))
    
    _logger.info("=== Finalizado post_init_hook do csw_totalpay ===")


def _add_methods_to_journals(env, mbway_method, paypal_method, multibanco_method):
    """Adiciona métodos aos diários bancários"""
    Journal = env['account.journal']
    PaymentMethodLine = env['account.payment.method.line']
    
    # Buscar diários bancários
    bank_journals = Journal.search([('type', '=', 'bank')])
    
    if not bank_journals:
        _logger.warning("⚠️  Nenhum diário bancário encontrado")
        return
    
    _logger.info("📋 Encontrados %d diário(s) bancário(s)", len(bank_journals))
    
    for journal in bank_journals:
        try:
            # Adicionar MB Way
            if not PaymentMethodLine.search([('journal_id', '=', journal.id), ('payment_method_id', '=', mbway_method.id)], limit=1):
                PaymentMethodLine.create({
                    'payment_method_id': mbway_method.id,
                    'journal_id': journal.id,
                })
                _logger.info("✅ MB Way adicionado ao diário '%s'", journal.name)
            
            # Adicionar PayPal
            if not PaymentMethodLine.search([('journal_id', '=', journal.id), ('payment_method_id', '=', paypal_method.id)], limit=1):
                PaymentMethodLine.create({
                    'payment_method_id': paypal_method.id,
                    'journal_id': journal.id,
                })
                _logger.info("✅ PayPal adicionado ao diário '%s'", journal.name)
            
            # Adicionar MULTIBANCO
            if not PaymentMethodLine.search([('journal_id', '=', journal.id), ('payment_method_id', '=', multibanco_method.id)], limit=1):
                PaymentMethodLine.create({
                    'payment_method_id': multibanco_method.id,
                    'journal_id': journal.id,
                })
                _logger.info("✅ MULTIBANCO adicionado ao diário '%s'", journal.name)
        except Exception as e:
            _logger.error("❌ Erro ao adicionar métodos ao diário '%s': %s", journal.name, str(e))
