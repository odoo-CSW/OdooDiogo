from odoo import models, fields, api


class CSWTotalPaySupport(models.Model):
    _name = "x_csw_totalpay_suporte"
    _description = "Total Pay Support Information"
    _rec_name = "x_studio_support_empresa"

    x_studio_support_empresa = fields.Char(
        string="Empresa",
        default="Comsoftweb - Sistemas Informáticos, Lda",
        readonly=True
    )
    
    x_studio_support_telefone = fields.Char(
        string="Nº Telefone",
        default="+351 236 210 600",
        readonly=True
    )
    
    x_studio_support_morada = fields.Char(
        string="Morada",
        default="Largo do Casal Galego P1 RC, 3100-522 Pombal",
        readonly=True
    )
    
    x_studio_support_email = fields.Char(
        string="E-mail",
        default="geral@comsoftweb.pt",
        readonly=True
    )
    
    x_studio_support_link_suporte = fields.Char(
        string="Link Formulário Suporte",
        default="https://www.comsoftweb.pt/support",
        readonly=True
    )
    
    x_studio_support_versao = fields.Char(
        string="Versão Módulo",
        default="1.0.0",
        readonly=True
    )
    
    x_studio_support_certificacao = fields.Char(
        string="Certificação",
        readonly=False
    )
    
    @api.model
    def get_suporte_info(self):
        """Return or create the unique support record."""
        suporte = self.search([], limit=1)
        if not suporte:
            suporte = self.create({})
        return suporte
    
    def action_open_suporte(self):
        """Action to open the unique support record."""
        suporte = self.get_suporte_info()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Suporte Técnico',
            'res_model': 'x_csw_totalpay_suporte',
            'res_id': suporte.id,
            'view_mode': 'form',
            'target': 'new',
            'context': {'form_view_initial_mode': 'readonly'},
        }
