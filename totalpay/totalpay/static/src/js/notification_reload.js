/** @odoo-module **/

import { registry } from "@web/core/registry";

function notificationReloadAction(env, action) {
    const params = action.params || {};
    const title = params.title || '';
    const message = params.message || '';
    const type = params.type || 'info';  // success, warning, danger, info
    const timeout = params.timeout || 2000;  // tempo em ms antes do reload
    
    // Mostrar notificação
    env.services.notification.add(message, {
        title: title,
        type: type,
        sticky: false,
    });
    
    // Recarregar página após o timeout
    setTimeout(() => {
        window.location.reload();
    }, timeout);
    
    return Promise.resolve();
}

// Registar a ação cliente
registry.category("actions").add("totalpay_notification_reload", notificationReloadAction);
