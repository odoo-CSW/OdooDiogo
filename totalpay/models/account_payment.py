# -*- coding: utf-8 -*-
from datetime import timedelta
from uuid import uuid4
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from . import constants
_logger = logging.getLogger(__name__)

class AccountPayment(models.Model):

    _inherit = ['account.payment', 'payment.method.mixin']
    _name = 'account.payment'
    
    state = fields.Selection(
        selection_add=[('in_process', 'In Process')],
        ondelete={'in_process': 'set default'}
    )
    
    x_mbway_is_active = fields.Boolean(
        string="MB WAY Ativo",
        compute='_compute_mbway_is_active',
        help="Indica se o pagamento MB WAY ainda está dentro do período de 4 minutos"
    )
    
    @api.depends('payment_method_code')
    def _compute_mbway_is_active(self):
        """Verifica se o pagamento MB WAY ainda está ativo (não expirou os 4 minutos)"""
        # Pré-carregar todos os connectors de uma vez para evitar N queries
        # e evitar chamar .search() dentro do loop de compute (antipadrão que
        # provoca flush implícito do ORM e contribui para "Too many iterations")
        payment_ids = self.ids
        connectors_by_payment = {}
        if payment_ids:
            connectors = self.env['x_csw_totalpay'].sudo().search([
                ('account_payment_id', 'in', payment_ids)
            ])
            for c in connectors:
                if c.account_payment_id.id not in connectors_by_payment:
                    connectors_by_payment[c.account_payment_id.id] = c

        now = fields.Datetime.now()
        for payment in self:
            payment.x_mbway_is_active = False

            if payment.payment_method_code != constants.PAYMENT_METHOD_MBWAY:
                continue

            connector = connectors_by_payment.get(payment.id)
            if not connector or not connector.x_studio_date_stop:
                continue

            if connector.x_studio_date_stop > now:
                payment.x_mbway_is_active = True

    @api.model_create_multi
    def create(self, vals_list):
        # Se houver pelo menos um pagamento com método TotalPay, validar licença antes de criar
        any_totalpay = False
        for vals in vals_list:
            method_code = self._get_method_code_from_create_vals(vals)
            if self._is_totalpay_method(method_code):
                any_totalpay = True
                break

        if any_totalpay:
            # Validar licença COM verificação no servidor (sem skip_webhook_sync)
            # para garantir que o estado está atualizado
            license_action = self._ensure_totalpay_license_active(skip_webhook_sync=False)
            if isinstance(license_action, dict):
                params = license_action.get('params', {})
                title = params.get('title', '')
                message = params.get('message', '')
                
                # Construir mensagem completa para o utilizador
                full_message = f"{title}\n\n{message}" if title and message else (message or title or _('Serviço indisponível'))
                raise UserError(full_message)

        # Validação prioritária: bloquear valores inválidos antes de criar
        self._validate_totalpay_amount_in_create_vals(vals_list)

        # Validação genérica: MB WAY só permite 1 pagamento por batch
        self._validate_mbway_batch(vals_list)
        return super().create(vals_list)
    
    def _get_method_code_from_create_vals(self, vals):
        method_line_id = vals.get('payment_method_line_id')
        if method_line_id:
            method_line = self.env['account.payment.method.line'].browse(method_line_id)
            if method_line.exists() and method_line.payment_method_id:
                return (method_line.payment_method_id.code or '').upper()

        if 'payment_method_code' in vals:
            return (vals.get('payment_method_code') or '').upper()

        return ''

    def _validate_totalpay_amount_in_create_vals(self, vals_list):
        for vals in vals_list:
            method_code = self._get_method_code_from_create_vals(vals)
            if not self._is_totalpay_method(method_code):
                continue

            amount = vals.get('amount')
            if amount is None:
                continue

            if amount <= 0:
                fmt = self._format_amount(amount)
                raise UserError(
                    _('Não é possível processar pagamentos TotalPay com valor inválido: %s') % fmt
                )

    def _validate_totalpay_amount_recordset(self):
        invalid_payments = self.filtered(lambda payment: payment._is_totalpay_method(payment._get_totalpay_method_code()) and payment.amount <= 0)
        if invalid_payments:
            # Construir mensagem com nomes e valores formatados
            parts = []
            for p in invalid_payments:
                parts.append('%s (%s)' % (p.name or 'N/A', self._format_amount(p.amount)))
            joined = ', '.join(parts)
            raise UserError(_('Não é possível processar pagamentos TotalPay com valor inválido: %s') % joined)

    def _format_amount(self, amount):
        try:
            return "{:.2f}€".format(float(amount))
        except Exception:
            return str(amount)

    def _validate_mbway_batch(self, vals_list):
        """Valida se há mais de 1 MB WAY no batch - válido para create"""
        mbway_count = 0

        for vals in vals_list:
            is_mbway = False

            # Verificar via payment_method_line_id
            method_line_id = vals.get('payment_method_line_id')
            if method_line_id:
                method_line = self.env['account.payment.method.line'].browse(method_line_id)
                if method_line.exists() and method_line.payment_method_id:
                    method_code = (method_line.payment_method_id.code or '').upper()
                    if method_code == constants.PAYMENT_METHOD_MBWAY:
                        is_mbway = True

            # Verificar via payment_method_code
            if not is_mbway and 'payment_method_code' in vals:
                if (vals.get('payment_method_code') or '').upper() == constants.PAYMENT_METHOD_MBWAY:
                    is_mbway = True

            if is_mbway:
                mbway_count += 1

        if mbway_count > 1:
            raise UserError(
                _('Não é possível gerar mais de 1 pagamento MB WAY em simultâneo.')
            )

        # A validação aqui deve atuar apenas no batch atual de create().
        # Não usar estado global do cursor/request para evitar falso positivo
        # em fluxos não-TotalPay (ex.: confirmação de orçamento/subscrições).

    def _get_planned_payment_count_from_context(self):
        planned_count = self.env.context.get('totalpay_planned_payment_count')
        if isinstance(planned_count, int):
            return planned_count if planned_count >= 0 else 0

        if isinstance(planned_count, str):
            try:
                parsed = int(planned_count)
                return parsed if parsed >= 0 else 0
            except Exception:
                pass

        batch_results = self.env.context.get('batch_result')
        if isinstance(batch_results, list):
            return len(batch_results)

        active_ids = self.env.context.get('active_ids')
        if isinstance(active_ids, (list, tuple, set)):
            return len(active_ids)

        return 0

    def _ensure_totalpay_license_active(self, skip_webhook_sync=False):
        if self.env.context.get('totalpay_license_checked'):
            return None
        
        config = self.env['x_csw_totalpay_config'].sudo().search([], limit=1)
        if not config:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('❌ Serviço indisponível'),
                    'message': _('Configuração TotalPay não encontrada.'),
                    'type': 'danger',
                    'sticky': False,
                }
            }

        if not skip_webhook_sync:
            try:
                sync_action = config.sudo().action_test_odoo_webhook()
                if isinstance(sync_action, dict):
                    params = sync_action.get('params', {})
                    if params.get('type') == 'danger':
                        # Marcar config como indisponível quando há erro
                        try:
                            config.sudo().write({'x_studio_status_check': 'semValor'})
                        except:
                            pass
                        return {
                            'type': 'ir.actions.client',
                            'tag': 'display_notification',
                            'params': {
                                'title': params.get('title', _('❌ Serviço indisponível')),
                                'message': params.get('message', _('Erro ao validar licença.')),
                                'type': 'danger',
                                'sticky': False,
                            }
                        }
            except Exception as e:
                # Marcar config como indisponível quando há exceção
                try:
                    config.sudo().write({'x_studio_status_check': 'semValor'})
                except:
                    pass

        if (config.x_studio_status_check or '').strip().lower() not in ['active', 'trial']:
            # Obter nome do estado para mensagem mais clara
            status_map = {
                'semValor': 'sem valor/indisponível',
                'canceled': 'cancelada',
                'cancelled': 'cancelada',
                'suspended': 'suspensa',
                'inactive': 'inativa',
                'trial': 'em trial',
                'active': 'ativa',
            }
            current_status = (config.x_studio_status_check or '').strip().lower()
            status_display = status_map.get(current_status, current_status or 'desconhecido')
            
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('❌ Serviço indisponível'),
                    'message': _('A licença TotalPay está %s.\n\nNão é possível processar pagamentos neste momento.\n\nPor favor, contacte o suporte para resolver esta situação.') % status_display,
                    'type': 'danger',
                    'sticky': True,
                }
            }
        
        return None
    
    def _prepare_connector_values(self, method_code, config, is_batch=False):
        self.ensure_one()
        
        # Não definir date_start/date_stop aqui - será definido com o timestamp da API no callback
        phone_value = self.x_mbway_phone or (self.partner_id.phone or '') if method_code == constants.PAYMENT_METHOD_MBWAY else ''
        email_value = self.x_paypal_email or (self.partner_id.email or '') if method_code == 'PAYPAL' else ''
        
        return {
            'x_name': self.name,
            'account_payment_id': self.id,
            'x_studio_partner_id': self.partner_id.id if self.partner_id else False,
            'x_studio_operador': self.create_uid.id if self.create_uid else False,
            'x_studio_operador_name': self.create_uid.name if self.create_uid else '',
            'x_studio_currency_id': self.currency_id.id if self.currency_id else False,
            'x_studio_value': self.amount,
            'x_studio_date_hour_payment': self.create_date,
            'x_studio_date_start': False,
            'x_studio_date_stop': False,
            'x_studio_metodo_pagamento': method_code,
            'x_studio_partner_phone': phone_value,
            'x_studio_partner_email': email_value,
            'x_studio_stage_id': constants.STAGE_PENDENTE,
            'x_studio_organization_id': config.x_studio_organization_id or '',
            'x_studio_key_api': config.x_studio_api_key or '',
            'x_studio_name': config.x_studio_name or '',
            'x_studio_nome_empresa_abreviado': config.x_studio_nome_empresa_abreviado or '',
            'x_studio_conf_modulo': config.id,
            'x_waiting_batch_process': is_batch,
        }

    def _get_totalpay_method_code(self):
        self.ensure_one()

        if self.payment_method_line_id and self.payment_method_line_id.payment_method_id:
            return (self.payment_method_line_id.payment_method_id.code or '').upper()

        if self.payment_method_code:
            return (self.payment_method_code or '').upper()

        return ''

    def _is_mbway_payment(self):
        self.ensure_one()
        return self._get_totalpay_method_code() == constants.PAYMENT_METHOD_MBWAY

    def _is_totalpay_method(self, method_code):
        return method_code in [
            constants.PAYMENT_METHOD_MBWAY,
            constants.PAYMENT_METHOD_MULTIBANCO,
            'PAYPAL',
            'CREDIT_CARD',
        ]

    def _get_or_create_totalpay_connector(self, method_code, config):
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.id)
        ], limit=1)

        if connector:
            return connector

        values = self._prepare_connector_values(method_code, config, is_batch=False)
        return self.env['x_csw_totalpay'].with_context(from_api=True).create(values)

    def _build_mbway_popup_action(self):
        self.ensure_one()
        
        # Buscar o connector para calcular tempo restante
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.id)
        ], limit=1)
        
        time_remaining = constants.MBWAY_TIMEOUT_MINUTES * 60  # Default: 4 minutos em segundos
        
        if connector and connector.x_studio_date_stop:
            # Calcular tempo restante real
            now = fields.Datetime.now()
            delta = connector.x_studio_date_stop - now
            time_remaining = max(0, int(delta.total_seconds()))

        wizard = self.env['mbway.timer.wizard'].create({
            'payment_id': self.id,
            'phone_number': self.x_mbway_phone or (self.partner_id.phone or ''),
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'payment_ref': self.name,
            'time_remaining': time_remaining,
        })

        return {
            'name': _('MB WAY - Aguardando Aprovação'),
            'type': 'ir.actions.act_window',
            'res_model': 'mbway.timer.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }

    def _build_multibanco_popup_action(self, connector):
        self.ensure_one()

        wizard = self.env['multibanco.wizard'].create({
            'payment_id': self.id,
            'entidade': connector.x_studio_mb_entidade,
            'referencia': connector.x_studio_mb_referencia,
            'valor': connector.x_studio_mb_valor or self.amount,
            'currency_id': self.currency_id.id,
            'payment_ref': self.name,
            'expiry_date': connector.x_studio_mb_expiry_date,
            'partner_id': self.partner_id.id if self.partner_id else False,
            'partner_email': self.partner_id.email if self.partner_id and self.partner_id.email else '',
            'connector_id': connector.id,
            'expiry_days_used': connector.x_studio_mb_expiry_days,
        })

        return {
            'name': _('MULTIBANCO - Instruções de Pagamento'),
            'type': 'ir.actions.act_window',
            'res_model': 'multibanco.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }

    def _build_multibanco_batch_popup_action(self, payments):
        Wizard = self.env['multibanco.batch.wizard']
        Line = self.env['multibanco.batch.wizard.line']

        wizard = Wizard.create({
            'total_payments': len(payments),
            'pending_payment_ids': False,
            'current_block': 1,
        })

        for payment in payments:
            connector = self.env['x_csw_totalpay'].search([
                ('account_payment_id', '=', payment.id)
            ], limit=1)
            if not connector:
                continue

            partner_email = payment.partner_id.email if payment.partner_id else ''
            
            # Se connector falhou OU não tem referências, desabilitar envio de email
            is_failed = connector.x_studio_stage_id.id == constants.STAGE_FALHOU
            has_references = bool(connector.x_studio_mb_referencia)
            can_send = has_references and not is_failed and bool(partner_email)

            Line.create({
                'wizard_id': wizard.id,
                'connector_id': connector.id,
                'partner_id': payment.partner_id.id if payment.partner_id else False,
                'partner_name': payment.partner_id.name if payment.partner_id else 'Cliente sem nome',
                'partner_email': partner_email,
                'send_email': can_send,
                'payment_ref': payment.name,
            })

        return {
            'name': f'Envio de Emails MULTIBANCO - {wizard.block_progress_info}',
            'type': 'ir.actions.act_window',
            'res_model': 'multibanco.batch.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'new',
        }

    def _get_recent_multibanco_group(self, seconds=10):
        self.ensure_one()

        threshold = fields.Datetime.now() - timedelta(seconds=seconds)
        candidates = self.search([
            ('create_uid', '=', self.env.uid),
            ('create_date', '>=', threshold),
        ])

        return candidates.filtered(
            lambda payment: payment._get_totalpay_method_code() == constants.PAYMENT_METHOD_MULTIBANCO
        )

    def _notify_totalpay_popup(self, payment_ids):
        if not payment_ids:
            return

        partner = self.env.user.partner_id
        payload = {
            'payment_ids': payment_ids,
            'uid': self.env.uid,
            'popup_uuid': str(uuid4()),
        }

        partner._bus_send('totalpay_popup', payload)

    @api.model
    def action_open_totalpay_popup(self, payment_ids):
        # Verificar licença antes de operar popups
        license_action = self._ensure_totalpay_license_active(skip_webhook_sync=True)
        if isinstance(license_action, dict):
            return license_action

        payments = self.browse(payment_ids).exists()
        if not payments:
            return False

        if len(payments) > 1:
            multibanco_payments = payments.filtered(
                lambda payment: payment._get_totalpay_method_code() == constants.PAYMENT_METHOD_MULTIBANCO
            )
            if len(multibanco_payments) == len(payments):
                # Abrir batch com TODOS os pagamentos (incluindo falhados)
                return multibanco_payments[0]._build_multibanco_batch_popup_action(multibanco_payments)
            return False

        payment = payments[0]
        method_code = payment._get_totalpay_method_code()
        
        if method_code == constants.PAYMENT_METHOD_MBWAY:
            # Verificar se connector não está falhado
            connector = self.env['x_csw_totalpay'].search([
                ('account_payment_id', '=', payment.id)
            ], limit=1)
            if connector and connector.x_studio_stage_id.id == constants.STAGE_FALHOU:
                return False
            return payment._build_mbway_popup_action()

        if method_code == constants.PAYMENT_METHOD_MULTIBANCO:
            connector = self.env['x_csw_totalpay'].search([
                ('account_payment_id', '=', payment.id)
            ], limit=1)
            
            # Validar: não falhado E tem referências
            if connector and connector.x_studio_stage_id.id != constants.STAGE_FALHOU and connector.x_studio_mb_referencia:
                return payment._build_multibanco_popup_action(connector)
            return False

        return False

    def _trigger_totalpay_integration(self):
        """Aciona integração TotalPay de forma síncrona"""
        processed_payments = self.env['account.payment']
        error_messages = []

        for payment in self:
            method_code = payment._get_totalpay_method_code()
            if not payment._is_totalpay_method(method_code):
                continue

            license_action = payment._ensure_totalpay_license_active()
            if isinstance(license_action, dict):
                continue

            config = payment.env['x_csw_totalpay_config'].sudo().search([], limit=1)
            if not config:
                continue

            try:
                connector = payment._get_or_create_totalpay_connector(method_code, config)
                result = connector.action_create_payment_request()
                
                # Verificar se retornou notificação de erro
                if isinstance(result, dict) and result.get('type') == 'ir.actions.client':
                    params = result.get('params', {})
                    if params.get('type') == 'danger':
                        # Acumular mensagens de erro
                        error_title = params.get('title', 'Erro')
                        error_msg = params.get('message', 'Erro desconhecido')
                        error_messages.append(f"{payment.name}: {error_msg}")
                        continue
                
                connector.invalidate_recordset()
                # Sempre notificar popup quando connector é criado/atualizado com sucesso
                processed_payments |= payment
            except Exception as e:
                _logger.error("Erro ao processar pagamento %s: %s", payment.name, str(e))
                error_messages.append(f"{payment.name}: Erro ao processar pagamento")
                continue

        # Se houver erros, mostrar ao utilizador
        if error_messages:
            full_error = '\n\n'.join(error_messages)
            raise UserError(_('❌ Erro ao processar pagamentos TotalPay:\n\n%s') % full_error)

        return processed_payments

    def action_post(self):
        # Validação prioritária: bloquear valor inválido antes de qualquer processamento
        self._validate_totalpay_amount_recordset()

        # Validação prioritária: bloquear pelo contexto antes de qualquer integração
        self._validate_mbway_batch_recordset()

        # ========== VALIDAÇÃO CAMPOS OBRIGATÓRIOS ==========
        
        if not self.env.context.get('totalpay_skip_auto'):
            for payment in self:
                method_code = payment._get_totalpay_method_code()
                
                # Validar telefone MB WAY obrigatório
                if method_code == constants.PAYMENT_METHOD_MBWAY:
                    if not payment.x_mbway_phone:
                        raise UserError(_('O número de telefone MB WAY é obrigatório.'))
                
                # Validar email PayPal obrigatório
                if method_code == 'PAYPAL':
                    if not payment.x_paypal_email:
                        raise UserError(_('O e-mail PayPal é obrigatório.'))
        
        # Criar pagamentos
        result = super().action_post()

        if self.env.context.get('totalpay_skip_auto'):
            return result

        # Processar integração TotalPay
        processed_payments = self._trigger_totalpay_integration()

        # Notificar popups apenas para pagamentos válidos e processados com sucesso
        if processed_payments:
            processed_payments._notify_totalpay_popups()

        return result

    def _validate_mbway_batch_recordset(self):
        """Valida se há mais de 1 MB WAY no recordset - válido para action_post e batch"""
        mbway_payments = self.filtered(lambda p: p._is_mbway_payment())

        if not mbway_payments:
            return

        if len(mbway_payments) > 1:
            raise UserError(
                _('Não é possível processar mais de 1 pagamento MB WAY em simultâneo.')
            )

        # A validação deve considerar apenas o recordset atual do post().
        # Evita bloquear fluxos externos que partilham o mesmo request.

    def _notify_totalpay_popups(self):
        """Decide quais popups notificar baseado nos métodos de pagamento"""
        if not self:
            return

        # Caso 1: Múltiplos pagamentos - apenas Multibanco batch
        if len(self) > 1:
            multibanco_payments = self.filtered(
                lambda p: p._get_totalpay_method_code() == constants.PAYMENT_METHOD_MULTIBANCO
            )
            # Se todos são Multibanco, filtrar apenas os válidos (não falhados)
            if len(multibanco_payments) == len(self):
                valid_payments = multibanco_payments.filtered(lambda p: not self._is_connector_failed(p))
                if valid_payments:
                    self._notify_totalpay_popup(valid_payments.ids)
            return

        # Caso 2: Pagamento único
        method_code = self._get_totalpay_method_code()
        
        # Verificar se connector falhou - não notificar
        if self._is_connector_failed(self):
            return
        
        # MB WAY: notificar imediatamente
        if method_code == constants.PAYMENT_METHOD_MBWAY:
            self._notify_totalpay_popup([self.id])
        
        # Multibanco: notificar sempre
        elif method_code == constants.PAYMENT_METHOD_MULTIBANCO:
            recent_group = self._get_recent_multibanco_group()
            # Filtrar grupo por não falhados
            valid_group = recent_group.filtered(lambda p: not self._is_connector_failed(p))
            if not valid_group:
                return
            # Se há grupo recente, notificar apenas no último pagamento
            if len(valid_group) > 1:
                if self.id == max(valid_group.ids):
                    self._notify_totalpay_popup(valid_group.ids)
            else:
                self._notify_totalpay_popup([self.id])

    def _is_connector_failed(self, payment):
        """Verifica se o connector do pagamento está em estado de falha"""
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', payment.id)
        ], limit=1)
        return connector and connector.x_studio_stage_id.id == constants.STAGE_FALHOU

    @api.model
    def action_process_totalpay_batch(self, payment_ids):
        """
        Processa batch de pagamentos TotalPay e notifica via bus.
        Usado por Server Actions (ex: confirmação de subscrições).
        
        Validações genéricas:
        - MB WAY: apenas 1 pagamento permitido
        - Multibanco: decide popup simples (1) ou batch (múltiplos)
        """
        # Validar licença antes de processar batches
        license_action = self._ensure_totalpay_license_active(skip_webhook_sync=True)
        if isinstance(license_action, dict):
            return license_action

        if not payment_ids:
            return {'type': 'ir.actions.act_window_close'}
            
        payments = self.browse(payment_ids).exists()
        if not payments:
            return {'type': 'ir.actions.act_window_close'}

        payments._validate_totalpay_amount_recordset()
        
        # Validação genérica: MB WAY só permite 1
        payments._validate_mbway_batch_recordset()
        
        # Notificar popups para todos os pagamentos TotalPay (sem filtrar por readiness)
        totalpay_payments = payments.filtered(lambda p: p._is_totalpay_method(p._get_totalpay_method_code()))
        if totalpay_payments:
            totalpay_payments._notify_totalpay_popups()
        
        return {'type': 'ir.actions.act_window_close'}

    def action_open_mbway_popup(self):
        """Abrir popup MB WAY para este pagamento"""
        self.ensure_one()
        # Validar licença antes de abrir popup
        license_action = self._ensure_totalpay_license_active(skip_webhook_sync=True)
        if isinstance(license_action, dict):
            return license_action

        return self._build_mbway_popup_action()

    def action_open_multibanco_popup(self):
        """Abrir popup Multibanco para este pagamento"""
        self.ensure_one()
        # Validar licença antes de abrir popup
        license_action = self._ensure_totalpay_license_active(skip_webhook_sync=True)
        if isinstance(license_action, dict):
            return license_action
        
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.id)
        ], limit=1)
        
        if not connector:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sem Referência'),
                    'message': _('Referência Multibanco ainda não gerada.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        if not (connector.x_studio_mb_entidade and connector.x_studio_mb_referencia):
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sem Dados'),
                    'message': _('Dados da referência Multibanco incompletos.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        return self._build_multibanco_popup_action(connector)
