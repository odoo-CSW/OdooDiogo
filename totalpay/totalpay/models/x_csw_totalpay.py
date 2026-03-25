from datetime import timedelta
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

from . import constants

_logger = logging.getLogger(__name__)


class CSWTotalPay(models.Model):
    _name = "x_csw_totalpay"
    _description = "CSW Pagamentos"
    _order = "create_date desc, id desc"
    _rec_name = "x_name"

    x_active = fields.Boolean(string="Ativo", default=True)
    x_color = fields.Integer(string="Cor")
    x_name = fields.Char(string="Número Pagamento", required=True)

    x_api_transaction_id = fields.Char(
        string="ID Transação API",
        readonly=True,
        help="ID retornado pela API para verificação de status",
    )

    account_payment_id = fields.Many2one(
        "account.payment",
        string="Pagamento Relacionado",
        ondelete="set null",
    )

    x_studio_organization_id = fields.Char(string="Organização ID")
    x_studio_api_version = fields.Char(string="Versão API")
    x_studio_key_api = fields.Char(string="API Key")

    x_studio_name = fields.Char(string="Empresa")
    x_studio_nome_empresa_abreviado = fields.Char(string="Nome Empresa Abreviado")

    x_studio_conf_modulo = fields.Many2one(
        "x_csw_totalpay_config",
        string="Configuração Módulo",
        ondelete="set null",
    )

    x_studio_partner_id = fields.Many2one(
        "res.partner",
        string="Contacto",
        ondelete="set null",
    )
    x_studio_partner_email = fields.Char(string="E-mail Contacto")
    x_studio_partner_phone = fields.Char(string="Telefone Contacto")

    x_studio_operador = fields.Many2one(
        "res.users",
        string="Operador",
        ondelete="set null",
    )
    x_studio_operador_name = fields.Char(string="Nome Operador")
    x_studio_user_id = fields.Many2one(
        "res.users",
        string="Responsável",
        ondelete="set null",
        domain=[("share", "=", False)],
    )

    x_studio_currency_id = fields.Many2one(
        "res.currency",
        string="Moeda",
        ondelete="set null",
    )
    x_studio_value = fields.Monetary(
        string="Valor",
        currency_field="x_studio_currency_id",
    )

    x_studio_metodo_pagamento = fields.Char(string="Método Pagamento")
    x_studio_error_message = fields.Text(string="Mensagem de Erro")

    x_studio_date = fields.Date(string="Data")
    x_studio_date_hour_payment = fields.Datetime(string="Data Pagamento")
    x_studio_date_hour_payment_approved = fields.Datetime(string="Data Aprovação")
    x_studio_date_start = fields.Datetime(string="Data Inicial")
    x_studio_date_stop = fields.Datetime(string="Data Final")
    x_last_status_check = fields.Datetime(string="Última Verificação Status")

    x_studio_payer_email = fields.Char(string="E-mail Pagamento")
    x_studio_payer_name = fields.Char(string="Nome Pagamento")

    x_studio_paypal_capture_id = fields.Char(string="PayPal Capture ID")
    x_studio_paypal_transaction_id = fields.Char(string="PayPal Transação ID")
    x_studio_payment_url = fields.Char(string="URL Pagamento")

    x_studio_mb_entidade = fields.Char(string="MB Entidade", readonly=True)
    x_studio_mb_referencia = fields.Char(string="MB Referência", readonly=True)
    x_studio_mb_valor = fields.Float(string="MB Valor", readonly=True)
    x_studio_mb_expiry_date = fields.Datetime(string="MB Data Expiração", readonly=True)

    x_studio_stage_id = fields.Many2one(
        "x_csw_totalpay_stage",
        string="Estado Pagamento",
        ondelete="set null",
    )
    x_studio_state_integrator = fields.Char(string="Estado Integrador")

    can_retry = fields.Boolean(
        string="Pode Repetir",
        compute="_compute_can_retry",
        store=False,
    )

    x_waiting_batch_process = fields.Boolean(
        string="Aguardando Processamento em Lote",
        default=False,
        help="Indica que este pagamento está na fila para envio à API",
    )

    x_studio_notes = fields.Html(string="Notas")

    @api.model
    def _search(self, args, offset=0, limit=None, order=None, **kwargs):
        if self.env.context.get('filter_today'):
            today = fields.Date.context_today(self)
            today_str = today.strftime('%Y-%m-%d')
            tomorrow_str = (today + timedelta(days=1)).strftime('%Y-%m-%d')
            args = [
                ('create_date', '>=', today_str + ' 00:00:00'),
                ('create_date', '<', tomorrow_str + ' 00:00:00'),
            ] + args
        return super()._search(args, offset=offset, limit=limit, order=order, **kwargs)

    @api.depends('x_studio_stage_id', 'account_payment_id', 'x_studio_date_hour_payment')
    def _compute_can_retry(self):
        for rec in self:
            if not rec.account_payment_id:
                rec.can_retry = False
            elif rec.x_studio_stage_id.id == constants.STAGE_FALHOU:
                rec.can_retry = True
            elif rec.x_studio_stage_id.id == constants.STAGE_PENDENTE and rec.x_studio_date_hour_payment:
                rec.can_retry = rec.x_studio_date_hour_payment <= fields.Datetime.now() - timedelta(minutes=1)
            else:
                rec.can_retry = False

    def _notify(self, title, message, ntype='info', sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': ntype, 'sticky': sticky},
        }

    def _ensure_single(self, label):
        if len(self) != 1:
            raise UserError(_("❌ Selecione apenas UM registo para: %s.") % label)
        self.ensure_one()

    def _get_config(self):
        return self.env['x_csw_totalpay_config'].sudo().search([], limit=1)

    def action_view_payment(self):
        self._ensure_single('Ver Pagamento')
        if not self.account_payment_id:
            raise UserError(_("❌ Não existe pagamento relacionado."))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Pagamento',
            'res_model': 'account.payment',
            'res_id': self.account_payment_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_send_multibanco_email(self):
        self._ensure_single('Enviar Email MULTIBANCO')
        if self.x_studio_metodo_pagamento != 'MULTIBANCO':
            return self._notify('⚠️ Método Inválido', 'Esta ação só está disponível para pagamentos MULTIBANCO', 'warning')
        if not self.x_studio_mb_entidade or not self.x_studio_mb_referencia:
            return self._notify('⚠️ Dados Incompletos', 'Não existem dados MULTIBANCO para enviar', 'warning')

        partner_email = self.x_studio_partner_email or (
            self.account_payment_id.partner_id.email
            if self.account_payment_id and self.account_payment_id.partner_id else None
        )
        wizard = self.env['multibanco.wizard'].create({
            'payment_id': self.account_payment_id.id if self.account_payment_id else False,
            'entidade': self.x_studio_mb_entidade,
            'referencia': self.x_studio_mb_referencia,
            'valor': self.x_studio_mb_valor or self.x_studio_value,
            'currency_id': self.x_studio_currency_id.id if self.x_studio_currency_id else False,
            'payment_ref': self.x_name,
            'expiry_date': self.x_studio_mb_expiry_date,
            'partner_id': self.account_payment_id.partner_id.id if self.account_payment_id and self.account_payment_id.partner_id else False,
            'connector_id': self.id,
            'partner_email': partner_email or '',
        })
        return {
            'name': _('MULTIBANCO - Reenviar Email'),
            'type': 'ir.actions.act_window',
            'res_model': 'multibanco.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get('from_api'):
            raise UserError(
                _("Não é possível criar pagamentos manualmente.\n"
                  "Os pagamentos são criados automaticamente pela integração CSW.")
            )
        return super().create(vals_list)

    def write(self, vals):
        allowed_fields = {
            'x_studio_stage_id',
            'x_studio_date_hour_payment_approved',
            'x_studio_date_hour_payment',
            'x_studio_api_version',
            'x_studio_payment_url',
            'x_studio_paypal_transaction_id',
            'x_studio_paypal_capture_id',
            'x_studio_payer_name',
            'x_studio_payer_email',
            'x_studio_state_integrator',
            'x_last_status_check',
            'x_studio_date_stop',
            'x_studio_error_message',
        }

        from_authorized_source = (
            self.env.context.get('from_api')
            or self.env.context.get('from_automation')
            or self.env.context.get('install_mode')
        )
        only_allowed_fields = set(vals.keys()).issubset(allowed_fields)

        if not from_authorized_source and not only_allowed_fields:
            raise UserError(
                _("Não é possível editar pagamentos manualmente.\n"
                  "Os pagamentos são geridos automaticamente pela integração CSW.")
            )

        return super().write(vals)

    def unlink(self):
        if not (self.env.context.get('from_api') or self.env.context.get('from_automation')):
            raise UserError(
                _("Não é possível eliminar pagamentos manualmente.\n"
                "Os pagamentos são geridos automaticamente pela integração CSW.")
            )
        return super().unlink()

    def _set_date_stop_if_missing(self):
        if not self.x_studio_date_stop:
            self.with_context(from_automation=True).sudo().write({
                'x_studio_date_stop': fields.Datetime.now()
            })

    def _cancel_related_payment(self):
        if not self.account_payment_id:
            return
        payment = self.account_payment_id.sudo()
        try:
            if payment.state in ('paid', 'draft', 'in_process'):
                payment.action_cancel()
        except Exception as e:
            _logger.error("Erro ao cancelar pagamento %s: %s", payment.name, str(e), exc_info=True)

    def _set_payment_failed(self, error_message):
        try:
            _logger.warning("[FALHA] %s: %s", self.x_name, error_message)
            self.with_context(from_api=True).write({
                'x_studio_stage_id': constants.STAGE_FALHOU,
                'x_studio_error_message': error_message,
            })
            self._cancel_related_payment()
        except Exception as e:
            _logger.error("[FALHA] Erro ao marcar pagamento como falhado: %s", str(e), exc_info=True)
