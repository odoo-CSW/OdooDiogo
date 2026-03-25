{
    "name": "Total Pay",
    "version": "1.2.28",
    "summary": "Integração Total Pay - Pagamentos MB WAY, Multibanco, PayPal e Cartão",
    "description": """
        Total Pay - Módulo de Integração de Pagamentos
        ================================================
        
        Funcionalidades:
        ----------------
        * ✅ MB WAY
        * ✅ Multibanco
        * ✅ PayPal
        * ✅ Cartão de Crédito
        * 📊 Gestão completa de estados de pagamento
        * 🔄 Verificação automática de status dos pagamentos
        
        Requisitos do Sistema:
        ----------------------
        ⚠️ **Linux On-Premise**: Requer Nginx configurado.
        
        ℹ️ Não necessário em: Odoo.sh ou Windows On-Premise
        
        Importante - Reembolsos e Cancelamentos:
        ----------------------------------------
        ⚠️ Reembolsos e cancelamentos de referências Multibanco apenas são possíveis através do Back Office.
        
        🌐 Aceda ao Back Office em: https://www.csw.pt
        
        Suporte Técnico:
        ----------------
        📧 Email: suporte@comsoftweb.pt
        🌐 Website: https://www.comsoftweb.pt
    """,
    "category": "Accounting",
    "author": "Comsoftweb",
    "website": "https://www.comsoftweb.pt",
    "depends": ["base", "base_automation", "account", "mail"],
"data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "data/x_csw_totalpay_stage_data.xml",
        "data/base_automation.xml",
        "data/email_template_multibanco.xml",
        "views/csw_totalpay_views.xml",
        "views/csw_totalpay_stage_views.xml",
        "views/csw_totalpay_config_views.xml",
        "views/csw_totalpay_suporte_views.xml",
        "views/account_payment_register_views.xml",
        "views/account_payment_views.xml",
        "views/mbway_timer_wizard_views.xml",
        "views/multibanco_wizard_views.xml",
        "views/multibanco_batch_wizard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "totalpay/static/src/js/mbway_timer.js",
            "totalpay/static/src/js/notification_reload.js",
            "totalpay/static/src/js/totalpay_popup_listener.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
    "license": "LGPL-3",
}
