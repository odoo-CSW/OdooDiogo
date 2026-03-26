# -*- coding: utf-8 -*-

from odoo import models, fields, api
from . import constants


class MbwayTimerWizard(models.TransientModel):
    _name = 'mbway.timer.wizard'
    _description = 'MB WAY - Cronômetro de Aprovação'

    payment_id = fields.Many2one('account.payment', string='Pagamento', required=True, readonly=True)
    phone_number = fields.Char(string='Número de Telefone', readonly=True)
    amount = fields.Monetary(string='Valor', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Moeda', readonly=True)
    payment_ref = fields.Char(string='Referência', readonly=True)
    
    # Campo para armazenar o tempo restante (em segundos)
    time_remaining = fields.Integer(
        string='Tempo Restante (segundos)',
        compute='_compute_time_remaining',
    )
    
    # Campo formatado para exibição (MM:SS)
    time_display = fields.Char(
        string='Tempo Display',
        compute='_compute_time_remaining',
    )
    
    @api.depends('payment_id')
    def _compute_time_remaining(self):
        """Calcula o tempo restante real baseado no x_studio_date_stop do connector"""
        for wizard in self:
            time_left = constants.MBWAY_TIMEOUT_MINUTES * 60  # Default
            
            if wizard.payment_id:
                # Buscar o connector para obter o date_stop
                connector = self.env['x_csw_totalpay'].search([
                    ('account_payment_id', '=', wizard.payment_id.id)
                ], limit=1)
                
                if connector and connector.x_studio_date_stop:
                    # Calcular tempo restante real
                    now = fields.Datetime.now()
                    date_stop = connector.x_studio_date_stop
                    
                    if date_stop > now:
                        delta = date_stop - now
                        time_left = int(delta.total_seconds())
                    else:
                        time_left = 0
            
            wizard.time_remaining = max(0, time_left)
            
            # Formatar para MM:SS
            minutes = time_left // 60
            seconds = time_left % 60
            wizard.time_display = f"{minutes:02d}:{seconds:02d}"
    
    def action_check_payment_status(self):
        self.ensure_one()
        
        # Buscar o pagamento Total Pay relacionado
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.payment_id.id)
        ], limit=1)
        
        if connector:
            stage_id = connector.x_studio_stage_id.id if connector.x_studio_stage_id else None
            
            # Aprovado
            if stage_id == constants.STAGE_APROVADO:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '✅ Pagamento Confirmado!',
                        'message': f'✅ O pagamento {self.payment_ref} foi aprovado com sucesso via MB WAY.',
                        'type': 'success',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            # Falhou
            elif stage_id == constants.STAGE_FALHOU:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '❌ Pagamento Falhou',
                        'message': f'O pagamento {self.payment_ref} não foi aprovado. Por favor, tente novamente.',
                        'type': 'danger',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            # Cancelado
            elif stage_id == constants.STAGE_CANCELADO:
                # Pagamento cancelado
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⚠️ Pagamento Cancelado',
                        'message': f'O pagamento {self.payment_ref} foi cancelado.',
                        'type': 'warning',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            else:
                # Ainda pendente ou em processamento
                stage_name = connector.x_studio_stage_id.x_name if connector.x_studio_stage_id else 'Desconhecido'
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '⏳ Aguardando Aprovação',
                        'message': f'Status atual: {stage_name}. Por favor, confirme o pagamento no seu telemóvel.',
                        'type': 'info',
                        'sticky': False,
                    }
                }
        else:
            # Connector não encontrado
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '⚠️ Processando',
                    'message': 'O pagamento ainda está sendo processado. Aguarde alguns segundos e tente novamente.',
                    'type': 'info',
                    'sticky': False,
                }
            }
    
    def action_cancel_payment(self):
        """
        Cancela o wizard
        """
        self.ensure_one()
        self.action_popup_abandoned()
        return {'type': 'ir.actions.act_window_close'}

    def action_popup_abandoned(self):
        """Marca o pagamento como cancelado quando o popup é fechado pelo utilizador."""
        self.ensure_one()

        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.payment_id.id)
        ], limit=1)

        if not connector:
            return False

        stage_id = connector.x_studio_stage_id.id if connector.x_studio_stage_id else None
        if stage_id in [constants.STAGE_PENDENTE, constants.STAGE_EM_PROCESSAMENTO]:
            connector.with_context(from_api=True).write({
                'x_studio_stage_id': constants.STAGE_CANCELADO,
                'x_studio_error_message': 'Fluxo MB WAY encerrado: popup/aba fechado pelo utilizador.',
            })
            return True

        return False
    
    def action_timeout(self):
        """
        Marca o pagamento como falhado quando o tempo expira
        Chamado quando o timer chega a 00:00
        """
        self.ensure_one()
        
        # Buscar o CSW Connector relacionado ao pagamento
        connector = self.env['x_csw_totalpay'].search([
            ('account_payment_id', '=', self.payment_id.id)
        ], limit=1)
        
        if connector:
            # Verificar se ainda está pendente ou em processamento
            if connector.x_studio_stage_id.id in [constants.STAGE_PENDENTE, constants.STAGE_EM_PROCESSAMENTO]:
                # Marcar como Falhado - as automações existentes cuidarão do resto
                connector.with_context(from_api=True).write({
                    'x_studio_stage_id': constants.STAGE_FALHOU,
                    'x_studio_error_message': f'Tempo de aprovação esgotado ({constants.MBWAY_TIMEOUT_MINUTES} minutos sem resposta)'
                })
                
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': '❌ Tempo Esgotado',
                        'message': f'O pagamento {self.payment_ref} excedeu o prazo de {constants.MBWAY_TIMEOUT_MINUTES} minutos e foi marcado como falhado.',
                        'type': 'danger',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
            else:
                # Já foi aprovado/cancelado, apenas informar
                stage_name = connector.x_studio_stage_id.x_name if connector.x_studio_stage_id else 'Desconhecido'
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'ℹ️ Tempo Esgotado',
                        'message': f'O tempo expirou, mas o pagamento já está no estado: {stage_name}',
                        'type': 'info',
                        'sticky': False,
                        'next': {'type': 'ir.actions.act_window_close'},
                    }
                }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '⚠️ Tempo Esgotado',
                    'message': 'O pagamento não foi encontrado no CSW Connector.',
                    'type': 'warning',
                    'sticky': False,
                    'next': {'type': 'ir.actions.act_window_close'},
                }
            }
