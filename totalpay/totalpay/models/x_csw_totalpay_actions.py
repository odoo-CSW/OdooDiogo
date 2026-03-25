import logging
from datetime import timedelta

from odoo import models, fields, _
from odoo.exceptions import UserError

from . import constants

_logger = logging.getLogger(__name__)


class CSWTotalPayActions(models.Model):
    _inherit = "x_csw_totalpay"

    def action_retry_payment(self):
        self._ensure_single('Repetir Pagamento')
        if not self.account_payment_id:
            raise UserError(_("❌ Este pagamento não tem um Pagamento relacionado."))

        now = fields.Datetime.now()
        stage_id = self.x_studio_stage_id.id
        if stage_id == constants.STAGE_FALHOU:
            pass
        elif stage_id == constants.STAGE_PENDENTE:
            if not self.x_studio_date_hour_payment or self.x_studio_date_hour_payment > now - timedelta(minutes=1):
                raise UserError(
                    _("❌ Este pagamento está pendente há menos de 1 minuto.\n"
                      "Aguarde pelo menos 1 minuto antes de repetir.")
                )
        else:
            raise UserError(
                _("❌ Não é possível repetir pagamentos no estado '%s'.\n"
                  "Apenas pagamentos 'Falhou' ou 'Pendente' (há mais de 1 minuto) podem ser repetidos.")
                % (self.x_studio_stage_id.x_name or "Sem estado")
            )

        try:
            payment = self.account_payment_id.sudo()
            is_mbway = (
                payment.payment_method_line_id
                and payment.payment_method_line_id.payment_method_id
                and payment.payment_method_line_id.payment_method_id.code.upper() == constants.PAYMENT_METHOD_MBWAY
            )
            if payment.state == 'posted':
                payment.action_cancel()
            if payment.state in ('cancel', 'cancelled', 'canceled'):
                payment.action_draft()

            nova_data = fields.Datetime.now()
            payment.write({'date': nova_data})
            self.with_context(from_api=True).write({
                'x_studio_stage_id': constants.STAGE_PENDENTE,
                'x_studio_date_hour_payment': nova_data,
                'x_studio_date_start': nova_data,
                'x_studio_date_stop': nova_data + timedelta(minutes=constants.MBWAY_TIMEOUT_MINUTES) if is_mbway else False,
                'x_studio_date_hour_payment_approved': False,
                'x_studio_paypal_capture_id': False,
                'x_studio_paypal_transaction_id': False,
                'x_studio_payment_url': False,
                'x_studio_payer_name': False,
                'x_studio_payer_email': False,
                'x_studio_error_message': False,
            })

            result = self.action_create_payment_request()
            self.invalidate_recordset(['x_studio_stage_id'])

            if self.x_studio_stage_id.id == constants.STAGE_FALHOU:
                return self._notify(
                    _('❌ Erro ao Repetir Pagamento'),
                    self.x_studio_error_message or 'Erro ao criar pedido',
                    'danger',
                )
            if isinstance(result, dict) and result.get('params', {}).get('type') == 'danger':
                return result

            if is_mbway:
                wizard = self.env['mbway.timer.wizard'].create({
                    'payment_id': payment.id,
                    'phone_number': payment.x_mbway_phone or self.x_studio_partner_phone or '',
                    'amount': payment.amount,
                    'currency_id': payment.currency_id.id,
                    'payment_ref': payment.name,
                })
                return {
                    'name': _('MB WAY - Aguardando Aprovação'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'mbway.timer.wizard',
                    'res_id': wizard.id,
                    'view_mode': 'form',
                    'target': 'new',
                }

            self.env.cr.commit()
            self.env['x_csw_totalpay'].invalidate_model(['x_studio_stage_id', 'x_studio_date_hour_payment'])
            return self._notify(_('✅ Pagamento Repetido'), _('O pedido de pagamento foi reenviado com sucesso.'), 'success')

        except Exception as e:
            _logger.error("[REPETIR PAGAMENTO] Erro: %s", e, exc_info=True)
            self.with_context(from_api=True).write({
                'x_studio_stage_id': constants.STAGE_FALHOU,
                'x_studio_error_message': f"Erro ao repetir: {e}",
            })
            return self._notify(_('❌ Erro ao Repetir Pagamento'), str(e), 'danger')

    def action_simulate_payment_approval(self):
        self._ensure_single('Simular Aprovação')
        if not self.account_payment_id:
            raise UserError(_("❌ Este pagamento não tem um Account Payment associado!"))
        if self.x_studio_stage_id.id != constants.STAGE_EM_PROCESSAMENTO:
            raise UserError(
                _("❌ Apenas pagamentos 'Em Processamento' podem ser aprovados!\n\nStage atual: %s")
                % self.x_studio_stage_id.x_name
            )
        self.with_context(from_api=True).write({
            'x_studio_stage_id': constants.STAGE_APROVADO,
            'x_studio_date_hour_payment_approved': fields.Datetime.now(),
        })
        result = self.action_mark_payment_approved()
        if result:
            return result
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': '🎯 Simulação Concluída',
                'message': f'Pagamento {self.x_name} processado com sucesso!',
                'type': 'success',
                'sticky': True,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            }
        }

    def _create_accounting_move(self, payment):
        config = self._get_config()
        receivable_account = (
            config.x_studio_reconcile_account_id
            if config and config.x_studio_reconcile_account_id
            else payment.destination_account_id
        )
        journal = payment.journal_id
        move_vals = {
            'date': payment.date,
            'ref': payment.name,
            'journal_id': journal.id,
            'currency_id': payment.currency_id.id,
            'partner_id': payment.partner_id.id,
            'line_ids': [
                (0, 0, {
                    'name': payment.name,
                    'partner_id': payment.partner_id.id,
                    'account_id': receivable_account.id,
                    'credit': payment.amount,
                    'debit': 0.0,
                    'currency_id': payment.currency_id.id,
                }),
                (0, 0, {
                    'name': payment.name,
                    'partner_id': payment.partner_id.id,
                    'account_id': journal.default_account_id.id,
                    'debit': payment.amount,
                    'credit': 0.0,
                    'currency_id': payment.currency_id.id,
                }),
            ],
        }
        move = self.env['account.move'].sudo().create(move_vals)
        move.action_post()
        payment.write({'move_id': move.id})
        _logger.info("[APROVAÇÃO] Lançamento contábil criado: %s", move.name)

    def _confirm_payment(self, payment):
        try:
            payment.with_context(totalpay_skip_auto=True).action_post()
        except Exception as state_error:
            _logger.error("[APROVAÇÃO] Erro ao confirmar pagamento: %s", str(state_error), exc_info=True)

    def _auto_reconcile(self, payment, config):
        if not config.x_studio_reconcile_account_id:
            return
        account = config.x_studio_reconcile_account_id
        if not payment.move_id:
            return
        reconciled_any = False
        for invoice in payment.reconciled_invoice_ids:
            invoice_line = invoice.line_ids.filtered(
                lambda l: l.account_id == account and l.debit > 0 and not l.reconciled
            )
            payment_line = payment.move_id.line_ids.filtered(
                lambda l: l.account_id == account and l.credit > 0 and not l.reconciled
            )
            if invoice_line and payment_line:
                (invoice_line | payment_line).reconcile()
                reconciled_any = True
        _logger.info("[RECONCILIAÇÃO] %s: %s", self.x_name or 'N/A', "SIM" if reconciled_any else "NAO")

    def action_mark_payment_approved(self):
        self.ensure_one()
        try:
            if self.x_studio_stage_id.id != constants.STAGE_APROVADO:
                return
            if not self.x_studio_date_hour_payment_approved:
                now = fields.Datetime.now()
                self.with_context(from_automation=True).sudo().write({
                    'x_studio_date_hour_payment_approved': now,
                    'x_studio_date_stop': now,
                })
            if not self.account_payment_id:
                _logger.error("[APROVAÇÃO] Sem account.payment relacionado!")
                return
            payment = self.account_payment_id.sudo()
            try:
                if payment.state in ('draft', 'in_process'):
                    if not payment.move_id:
                        self._create_accounting_move(payment)
                    self._confirm_payment(payment)
                    config = self._get_config()
                    if config and config.x_studio_auto_reconcile and payment.partner_id:
                        try:
                            self._auto_reconcile(payment, config)
                        except Exception as rec_error:
                            _logger.error("[RECONCILIAÇÃO] ERRO: %s", str(rec_error), exc_info=True)
            except Exception as e:
                _logger.error("[APROVAÇÃO] ERRO CRÍTICO no pagamento %s: %s", payment.name, str(e), exc_info=True)
                raise
        except Exception as e:
            _logger.error("[AUTOMACAO] ERRO na automação %s: %s", self.x_name, str(e), exc_info=True)
            raise

    def _handle_terminal_stage(self, expected_stage_id, stage_label, message):
        self.ensure_one()
        _logger.debug("[AUTOMACAO] action_mark_payment_%s INICIADA para pagamento %s (ID: %s)", stage_label, self.x_name, self.id)
        try:
            if self.x_studio_stage_id.id != expected_stage_id:
                _logger.debug("Stage atual (%s) diferente de %s (%s). Abortando.", self.x_studio_stage_id.id, stage_label, expected_stage_id)
                return

            _logger.info(message, self.x_name)
            self._set_date_stop_if_missing()
            self._cancel_related_payment()
        except Exception as e:
            _logger.error(
                "Erro ao processar %s do pagamento CSW (%s): %s",
                stage_label,
                self.x_name,
                str(e),
                exc_info=True
            )

    def action_mark_payment_terminal(self):
        stage_map = {
            constants.STAGE_CANCELADO: ("canceled", "Pagamento CSW %s movido para estado Cancelado"),
            constants.STAGE_FALHOU: ("failed", "Não foi possível repetir o pagamento %s"),
        }

        stage_info = stage_map.get(self.x_studio_stage_id.id)
        if not stage_info:
            _logger.debug("Stage atual (%s) nao e terminal. Ignorando.", self.x_studio_stage_id.id)
            return

        stage_label, message = stage_info
        self._handle_terminal_stage(self.x_studio_stage_id.id, stage_label, message)

    def action_send_payment_webhook(self):
        self.ensure_one()
        try:
            self.action_create_payment_request()
        except Exception as e:
            self._set_payment_failed(f"Erro ao criar pedido de pagamento: {str(e)}")
            _logger.error("[WEBHOOK] Erro para %s: %s", self.x_name, str(e), exc_info=True)
