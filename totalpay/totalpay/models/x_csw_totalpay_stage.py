from odoo import models, fields, api


class CSWTotalPayStage(models.Model):
    _name = "x_csw_totalpay_stage"
    _description = "Estado Pagamento"
    _order = "x_studio_sequence asc, id asc"
    _rec_name = "x_name"

    x_name = fields.Char(string="Estado Pagamento", required=True)
    x_studio_sequence = fields.Integer(string="Sequência", default=10)
