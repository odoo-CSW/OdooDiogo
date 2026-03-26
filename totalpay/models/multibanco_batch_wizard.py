# -*- coding: utf-8 -*-

from odoo import models, fields, api
import logging
from . import constants
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class MultibancoBatchWizard(models.TransientModel):
    _name = 'multibanco.batch.wizard'
    _description = 'MULTIBANCO - Envio de Emails em Lote'

    line_ids = fields.One2many('multibanco.batch.wizard.line', 'wizard_id', string='Clientes')
    total_payments = fields.Integer(string='Total de Pagamentos', readonly=True)
    
    # Controle de blocos
    pending_payment_ids = fields.Many2many('account.payment', string='Pagamentos Pendentes')
    current_block = fields.Integer(string='Bloco Atual', default=1, readonly=True)
    total_blocks = fields.Integer(string='Total de Blocos', compute='_compute_block_info', readonly=True)
    block_progress_info = fields.Char(string='Progresso', compute='_compute_block_info', readonly=True)
    has_remaining_payments = fields.Boolean(string='Tem Pagamentos Restantes', compute='_compute_block_info', readonly=True)
    
    # Contador acumulado de emails
    emails_sent_accumulated = fields.Integer(string='Emails Enviados (Acumulado)', default=0, readonly=True)
    emails_failed_accumulated = fields.Integer(string='Emails Falhados (Acumulado)', default=0, readonly=True)
    
    # Estatísticas
    success_count = fields.Integer(string='Sucessos', compute='_compute_statistics', readonly=True)
    failed_count = fields.Integer(string='Falhas', compute='_compute_statistics', readonly=True)
    has_failures = fields.Boolean(string='Tem Falhas', compute='_compute_statistics', readonly=True)
    
    @api.depends('line_ids', 'line_ids.status_symbol')
    def _compute_statistics(self):
        """Contar sucessos e falhas"""
        for wizard in self:
            success = sum(1 for line in wizard.line_ids if '✅' in (line.status_symbol or ''))
            failed = sum(1 for line in wizard.line_ids if '❌' in (line.status_symbol or ''))
            wizard.success_count = success
            wizard.failed_count = failed
            wizard.has_failures = failed > 0
    
    @api.depends('pending_payment_ids', 'current_block')
    def _compute_block_info(self):
        """Calcular informação sobre blocos"""
        for wizard in self:
            limit = constants.RATE_LIMIT_PAYMENTS
            total_pending = len(wizard.pending_payment_ids)
            
            if total_pending > 0:
                wizard.has_remaining_payments = True
                # Calcular total de blocos incluindo o bloco atual
                wizard.total_blocks = wizard.current_block + ((total_pending + limit - 1) // limit)
                wizard.block_progress_info = f'Bloco {wizard.current_block} de {wizard.total_blocks} - Restam {total_pending} pagamentos'
            else:
                wizard.has_remaining_payments = False
                wizard.total_blocks = wizard.current_block
                current_lines = len(wizard.line_ids)
                if current_lines > 0:
                    wizard.block_progress_info = (
                        f'Bloco {wizard.current_block} de {wizard.total_blocks} '
                        f'- Processar {current_lines} pagamento(s)'
                    )
                else:
                    wizard.block_progress_info = (
                        f'Bloco {wizard.current_block} de {wizard.total_blocks} - Completo'
                    )

    def action_send_emails(self):
        """Enviar emails para os clientes selecionados"""
        self.ensure_one()
        
        # Buscar template de email
        template = self.env.ref('totalpay.email_template_multibanco', raise_if_not_found=False)
        if not template:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '❌ Template não encontrado',
                    'message': 'Template de email MULTIBANCO não encontrado',
                    'type': 'danger',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                },
            }
        
        sent_count = 0
        failed_count = 0
        
        # Percorrer linhas e enviar emails
        for line in self.line_ids:
            # Validar: só enviar se send_email=True, tem email E connector não falhou
            if not line.send_email or not line.partner_email:
                continue
            
            # Validação extra: não enviar se connector está em estado de falha
            if line.connector_id.x_studio_stage_id.id == constants.STAGE_FALHOU:
                _logger.warning("[EMAIL BATCH] ⚠️ Pulando %s: pagamento falhado", line.partner_name)
                failed_count += 1
                continue
            
            # Validação: só enviar se tiver referências válidas
            if not line.connector_id.x_studio_mb_referencia:
                _logger.warning("[EMAIL BATCH] ⚠️ Pulando %s: sem referências Multibanco", line.partner_name)
                failed_count += 1
                continue
            
            try:
                # Preparar valores do email
                mail_values = {'email_to': line.partner_email}
                
                # Verificar servidor de email
                config = self.env['x_csw_totalpay_config'].sudo().search([], limit=1)
                if config and config.x_studio_mail_server_id:
                    mail_server = config.x_studio_mail_server_id
                    if mail_server.smtp_user:
                        mail_values['email_from'] = mail_server.smtp_user
                        mail_values['mail_server_id'] = mail_server.id
                
                # Enviar email
                mail_id = template.send_mail(
                    line.connector_id.id,
                    force_send=True,
                    email_values=mail_values
                )

                mail = self.env['mail.mail'].sudo().browse(mail_id).exists()
                if not mail:
                    _logger.error("❌ Email não gerado")
                    failed_count += 1
                    continue

                if mail.state != 'sent':
                    reason = mail.failure_reason or ''
                    _logger.error("❌ Email não enviado")
                    failed_count += 1
                    continue

                sent_count += 1
                _logger.info("✅ Email enviado para %s (%s)", line.partner_name, line.partner_email)
                
            except Exception as e:
                _logger.error("❌ Erro ao enviar para %s: %s", line.partner_email, str(e))
                failed_count += 1
        
        # Calcular totais acumulados
        total_emails_sent = self.emails_sent_accumulated + sent_count
        total_emails_failed = self.emails_failed_accumulated + failed_count
        
        # Verificar se há blocos restantes
        if self.pending_payment_ids:
            return self._open_next_block(sent_count, failed_count)
        
        # Mensagem final
        msg = f'{self.total_payments} pagamentos processados. '
        if total_emails_sent > 0:
            msg += f'{total_emails_sent} emails enviados. '
        if total_emails_failed > 0:
            msg += f'{total_emails_failed} falharam.'
        notif_type = 'warning' if total_emails_failed > 0 else 'success'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '✅ Processamento Completo',
                'message': msg,
                'type': notif_type,
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
    
    def action_skip_emails(self):
        """Pular envio de emails e processar próximo bloco se houver"""
        self.ensure_one()

        return {'type': 'ir.actions.act_window_close'}

    @api.model
    def _process_batch_block(self, payments):
        """Garante connectors e pedidos API para um bloco de pagamentos"""
        if not isinstance(payments, models.Model):
            payment_ids = payments or []
            payments = self.env['account.payment'].browse(payment_ids)
        payments = payments.filtered(lambda p: p)
        if not payments:
            return self.env['x_csw_totalpay'], 0, 0

        invalid_payments = payments.filtered(lambda payment: payment.amount <= 0)
        if invalid_payments:
            parts = []
            for p in invalid_payments:
                amt = p.amount if p.amount is not None else 0
                parts.append('%s (%s)' % (p.name or 'N/A', "{:.2f}€".format(float(amt))))
            refs = ', '.join(parts)
            raise UserError(
                f"Não é possível processar pagamentos MULTIBANCO com valor inválido: {refs}"
            )

        Connector = self.env['x_csw_totalpay']
        connectors = Connector.browse()
        success_count = 0
        failed_count = 0

        for payment in payments:
            try:
                connector = Connector.search([
                    ('account_payment_id', '=', payment.id)
                ], limit=1)

                if not connector:
                    # Usar método do próprio pagamento para criar connector
                    connector = payment._create_connector_for_direct_payment(is_batch=False)

                if not connector:
                    _logger.error("[BATCH WIZARD] Falha ao criar connector para %s", payment.name)
                    failed_count += 1
                    continue

                needs_request = True
                if connector.x_studio_metodo_pagamento == constants.PAYMENT_METHOD_MULTIBANCO:
                    if connector.x_studio_mb_entidade and connector.x_studio_mb_referencia:
                        needs_request = False
                
                # Se connector está em estado de falha, pular
                if connector.x_studio_stage_id.id == constants.STAGE_FALHOU:
                    _logger.warning("[BATCH WIZARD] Connector %s está em estado de falha, pulando", connector.x_name)
                    failed_count += 1
                    continue

                if needs_request:
                    _logger.info(
                        "[BATCH WIZARD] Enviando pedido TotalPay para connector %s",
                        connector.x_name
                    )
                    connector.action_create_payment_request()
                    connector.invalidate_recordset()
                    
                    # Verificar se criação falhou
                    if connector.x_studio_stage_id.id == constants.STAGE_FALHOU:
                        _logger.warning("[BATCH WIZARD] Criação de pedido falhou para %s", connector.x_name)
                        failed_count += 1
                        continue

                success_count += 1
                connectors |= connector

            except Exception as e:
                failed_count += 1
                _logger.error(
                    "[BATCH WIZARD] Erro ao processar pagamento %s: %s",
                    payment.name,
                    str(e)
                )

        _logger.info("[BATCH WIZARD] Processamento completo: %d conectores | %d sucessos | %d falhas",
                     len(connectors), success_count, failed_count)
        
        return connectors, success_count, failed_count
    
    def _open_next_block(self, emails_sent_this_block=0, emails_failed_this_block=0):
        """Processar próximo bloco de pagamentos"""
        self.ensure_one()
        
        # Calcular totais acumulados
        total_sent = self.emails_sent_accumulated + emails_sent_this_block
        total_failed = self.emails_failed_accumulated + emails_failed_this_block
        
        limit = constants.RATE_LIMIT_PAYMENTS
        remaining_payments = self.pending_payment_ids
        
        _logger.info("[BATCH WIZARD] _open_next_block: Limite=%d, Restantes=%d", limit, len(remaining_payments))
        _logger.info("[BATCH WIZARD] IDs dos pagamentos restantes: %s", remaining_payments.ids)
        
        if not remaining_payments:
            _logger.info("[BATCH WIZARD] Sem pagamentos restantes, fechando wizard")
            return {'type': 'ir.actions.act_window_close'}
        
        # Separar próximo bloco
        next_batch_payments = remaining_payments[:limit]
        remaining_after = remaining_payments[limit:]

        _logger.info("[BATCH WIZARD] Processando próximo bloco: %d pagamentos. Restam após: %d", 
                     len(next_batch_payments), len(remaining_after))
        
        connectors, success_count, failed_count = self._process_batch_block(next_batch_payments)
        _logger.info("[BATCH WIZARD] Bloco processado. Sucesso: %d | Falhas: %d", success_count, failed_count)

        if not connectors:
            connectors = self.env['x_csw_totalpay'].search([
                ('account_payment_id', 'in', next_batch_payments.ids)
            ])
        
        # Criar wizard para próximo bloco
        next_wizard = self.env['multibanco.batch.wizard'].create({
            'total_payments': self.total_payments,
            'pending_payment_ids': [(6, 0, remaining_after.ids)] if remaining_after else False,
            'current_block': self.current_block + 1,
            'emails_sent_accumulated': total_sent,
            'emails_failed_accumulated': total_failed,
        })
        
        # Criar linhas para o wizard
        for payment in next_batch_payments:
            connector = connectors.filtered(lambda c: c.account_payment_id.id == payment.id)
            if connector:
                partner_email = payment.partner_id.email if payment.partner_id else ''
                
                self.env['multibanco.batch.wizard.line'].create({
                    'wizard_id': next_wizard.id,
                    'connector_id': connector.id,
                    'partner_id': payment.partner_id.id if payment.partner_id else False,
                    'partner_name': payment.partner_id.name if payment.partner_id else 'Cliente sem nome',
                    'partner_email': partner_email,
                    'send_email': bool(partner_email),
                    'payment_ref': payment.name,
                })
        
        # Retornar ação para abrir próximo wizard
        return {
            'name': f'Envio de Emails MULTIBANCO - {next_wizard.block_progress_info}',
            'type': 'ir.actions.act_window',
            'res_model': 'multibanco.batch.wizard',
            'res_id': next_wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class MultibancoBatchWizardLine(models.TransientModel):
    _name = 'multibanco.batch.wizard.line'
    _description = 'MULTIBANCO - Linha de Email em Lote'

    wizard_id = fields.Many2one('multibanco.batch.wizard', string='Wizard', required=True, ondelete='cascade')
    connector_id = fields.Many2one('x_csw_totalpay', string='Connector', required=True, readonly=True)
    partner_id = fields.Many2one('res.partner', string='Cliente', readonly=True)
    partner_name = fields.Char(string='Nome do Cliente', readonly=True)
    partner_email = fields.Char(string='Email', help='Edite o email se necessário')
    send_email = fields.Boolean(string='Enviar Email?', default=True)
    payment_ref = fields.Char(string='Referência', readonly=True)
    status_symbol = fields.Char(string='Estado', compute='_compute_status_symbol', readonly=True)
    is_failed = fields.Boolean(string='Falhou', compute='_compute_is_failed', readonly=True)

    @api.depends('connector_id', 'connector_id.x_studio_stage_id')
    def _compute_is_failed(self):
        """Indica se o connector está em estado de falha"""
        for line in self:
            line.is_failed = (
                line.connector_id 
                and line.connector_id.x_studio_stage_id 
                and line.connector_id.x_studio_stage_id.id == constants.STAGE_FALHOU
            )

    @api.depends(
        'connector_id',
        'connector_id.x_studio_stage_id',
        'connector_id.x_studio_mb_entidade',
        'connector_id.x_studio_mb_referencia'
    )
    def _compute_status_symbol(self):
        for line in self:
            connector = line.connector_id
            if not connector:
                line.status_symbol = '❌ Sem dados'
                continue
            if connector.x_studio_stage_id and connector.x_studio_stage_id.id == constants.STAGE_FALHOU:
                line.status_symbol = '❌ Falhou'
                continue
            if connector.x_studio_mb_entidade and connector.x_studio_mb_referencia:
                line.status_symbol = '✅ OK'
            else:
                line.status_symbol = '⏳ Pendente'
