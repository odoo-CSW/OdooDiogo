# -*- coding: utf-8 -*-

from odoo import models, fields, api
import logging
from . import constants

_logger = logging.getLogger(__name__)


class MultibancoWizard(models.TransientModel):
    _name = 'multibanco.wizard'
    _description = 'MULTIBANCO - Instruções de Pagamento'

    payment_id = fields.Many2one('account.payment', string='Pagamento', required=True, readonly=True)
    entidade = fields.Char(string='Entidade', readonly=True)
    referencia = fields.Char(string='Referência', readonly=True)
    valor = fields.Float(string='Valor', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Moeda', readonly=True)
    payment_ref = fields.Char(string='Referência Pagamento', readonly=True)
    expiry_date = fields.Datetime(string='Data de Expiração', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Cliente', readonly=True)
    connector_id = fields.Many2one('x_csw_totalpay', string='CSW Connector', readonly=True)
    partner_email = fields.Char(string='Email Destinatário', help='Email para enviar as instruções de pagamento')
    days_remaining = fields.Integer(string='Dias Restantes', compute='_compute_days_remaining')
    expiry_message = fields.Char(string='Mensagem de Validade', compute='_compute_days_remaining')
    expiry_days_used = fields.Integer(string='Dias de Validade', readonly=True, help='Número de dias de validade usado ao gerar esta referência')
    
    @api.depends('expiry_date')
    def _compute_days_remaining(self):
        for record in self:
            if not record.expiry_date:
                record.days_remaining = 0
                record.expiry_message = 'Sem data de expiração'
                continue

            delta = record.expiry_date - fields.Datetime.now()

            if delta.total_seconds() < 0:
                record.days_remaining = 0
                record.expiry_message = 'Referência expirada'
            elif delta.days == 0:
                record.days_remaining = 0
                record.expiry_message = 'Último dia - expira hoje'
            elif delta.days == 1:
                record.days_remaining = 1
                record.expiry_message = 'Resta 1 dia'
            else:
                record.days_remaining = delta.days
                record.expiry_message = f'Restam {delta.days} dias'
    
    @api.model
    def default_get(self, fields_list):
        """Preencher email do parceiro e dias de validade por padrão"""
        res = super(MultibancoWizard, self).default_get(fields_list)
        
        # Buscar partner_id do context ou do payment_id
        partner_id = res.get('partner_id')
        if not partner_id and res.get('payment_id'):
            payment = self.env['account.payment'].browse(res['payment_id'])
            if payment.partner_id:
                partner_id = payment.partner_id.id
                res['partner_id'] = partner_id
        
        # Preencher email do partner
        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            if partner.email and 'partner_email' in fields_list:
                res['partner_email'] = partner.email
        
        # Preencher dias de validade do connector (valor fixo gravado na BD)
        if 'expiry_days_used' in fields_list and res.get('connector_id'):
            connector = self.env['x_csw_totalpay'].browse(res['connector_id'])
            if connector.x_studio_mb_expiry_days:
                res['expiry_days_used'] = connector.x_studio_mb_expiry_days
            else:
                # Fallback: usar valor da configuração atual
                config = self.env['x_csw_totalpay_config'].sudo().search([], limit=1)
                res['expiry_days_used'] = config.x_studio_multibanco_expiry_days if config else 30
        
        return res
    
    def action_send_email(self):
        """Enviar email com dados do MULTIBANCO para o cliente"""
        self.ensure_one()
        
        # Validar se connector existe e tem referências
        if not self.connector_id or not self.connector_id.x_studio_mb_referencia:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '❌ Sem Referências',
                    'message': 'Não é possível enviar email: referências Multibanco não geradas.',
                    'type': 'danger',
                    'sticky': False,
                }
            }
        
        # Validar se connector não está em estado de falha
        if self.connector_id.x_studio_stage_id.id == constants.STAGE_FALHOU:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '❌ Pagamento Falhado',
                    'message': 'Não é possível enviar email: o pagamento falhou.',
                    'type': 'danger',
                    'sticky': False,
                }
            }
        
        # Se não tiver email, apenas fechar o popup
        if not self.partner_email:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'ℹ️ Email não enviado',
                    'message': 'Nenhum email foi especificado',
                    'type': 'info',
                    'sticky': False,
                }
            }
        
        # Buscar template de email
        template = self.env.ref('totalpay.email_template_multibanco', raise_if_not_found=False)
        if template and self.connector_id:
            try:
                # Preparar valores do email
                mail_values = {
                    'email_to': self.partner_email,
                }
                
                # Verificar se há servidor de email configurado nas configs do módulo Total Pay
                config = self.env['x_csw_totalpay_config'].sudo().search([], limit=1)
                if config and config.x_studio_mail_server_id:
                    mail_server = config.x_studio_mail_server_id
                    if mail_server.smtp_user:
                        mail_values['email_from'] = mail_server.smtp_user
                        mail_values['mail_server_id'] = mail_server.id
                        _logger.info("[EMAIL] ✅ Usando servidor configurado no Total Pay: %s (%s)", 
                                    mail_server.name, mail_server.smtp_user)
                    else:
                        _logger.warning("[EMAIL] ⚠️ Servidor selecionado não tem smtp_user configurado")
                else:
                    # Deixar o Odoo usar o servidor SMTP padrão
                    _logger.info("[EMAIL] Nenhum servidor configurado no módulo, usando servidor SMTP padrão")
                
                # Gerar e enviar email
                mail_id = template.send_mail(
                    self.connector_id.id,
                    force_send=True,
                    email_values=mail_values
                )

                if not mail_id:
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': '❌ Email não enviado',
                            'message': 'O email não foi gerado pelo sistema.',
                            'type': 'danger',
                            'sticky': False,
                        }
                    }

                mail = self.env['mail.mail'].sudo().browse(mail_id).exists()
                if mail:
                    if mail.state != 'sent':
                        reason = mail.failure_reason or ''
                        state_label = mail.state or 'unknown'
                        return {
                            'type': 'ir.actions.client',
                            'tag': 'display_notification',
                            'params': {
                                'title': '❌ Email não enviado',
                                'type': 'danger',
                                'sticky': False,
                            }
                        }
                # mail.mail can be auto-deleted right after send; treat as success if send_mail returned an id.

                # Fechar o popup e mostrar notificação de sucesso
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Email enviado',
                        'message': f'Instruções de pagamento enviadas para {self.partner_email}',
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            except Exception as e:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '❌ Erro ao enviar email',
                        'message': 'Não foi possível enviar o email. Verifique a configuração de email.',
                        'type': 'danger',
                        'sticky': False,
                    }
                }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '❌ Template não encontrado',
                    'message': 'Template de email MULTIBANCO não encontrado',
                    'type': 'danger',
                    'sticky': False,
                }
            }
    
    def action_close(self):
        """Fechar wizard"""
        return {'type': 'ir.actions.act_window_close'}
