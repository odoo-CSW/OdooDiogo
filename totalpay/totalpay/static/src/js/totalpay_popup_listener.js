/** @odoo-module **/

import { registry } from "@web/core/registry";
import { session } from "@web/session";

const totalpayPopupListener = {
    dependencies: ["bus_service", "orm", "action"],
    start(env) {
        const busService = env.services.bus_service;
        const orm = env.services.orm;
        const action = env.services.action;

        // Se bus_service não está disponível, não inicializar listener
        // A validação será mostrada apenas se o serviço não funcionar
        if (!busService) {
            console.info('Popups não disponíveis para a aplicação TotalPay: Contacte o administrador do sistema.');
            return;
        }

        const userId = session.uid;
        
        // Rastrear IDs de notificações já processadas
        const processedNotifications = new Set();
        
        // Rastrear popups atualmente abertos para evitar duplicados
        const openPopups = new Set(); // Set de payment IDs

        // --- Coordenação entre abas ---
        // Web Locks API: exclusão mútua real entre abas (sem race conditions).
        // Fallback: localStorage (para browsers muito antigos).
        async function tryClaimPopup(claimKey) {
            if (!claimKey) return true;

            // Preferir Web Locks API (Chrome 69+, Firefox 96+, Safari 15.4+)
            if (typeof navigator !== 'undefined' && navigator.locks) {
                return new Promise(resolve => {
                    navigator.locks.request(
                        'totalpay_popup_' + claimKey,
                        { ifAvailable: true },
                        async (lock) => {
                            if (!lock) {
                                resolve(false); // Outra aba já tem o lock
                                return;
                            }
                            resolve(true); // Esta aba ganhou
                            // Manter lock por 10s para bloquear duplicados
                            await new Promise(r => setTimeout(r, 10000));
                        }
                    );
                });
            }

            // Fallback: localStorage (com race condition residual)
            const key = 'totalpay_popup_claim_' + claimKey;
            const now = Date.now();
            try {
                const existingClaim = localStorage.getItem(key);
                if (existingClaim && (now - parseInt(existingClaim)) < 10000) {
                    return false;
                }
                localStorage.setItem(key, now.toString());
                setTimeout(() => {
                    try { localStorage.removeItem(key); } catch (e) {}
                }, 30000);
                return true;
            } catch (e) {
                return true;
            }
        }

        // --- Intercetar o fecho de popups ---
        const originalDoAction = action.doAction;
        action.doAction = function (actionRequest, options) {
            if (actionRequest && actionRequest.type === 'ir.actions.act_window_close' && openPopups.size > 0) {
                window.location.reload();
            }
            return originalDoAction.apply(this, arguments);
        };

        // Subscrever diretamente ao tipo de notificação 'totalpay_popup'
        busService.subscribe('totalpay_popup', async (payload, { id }) => {
            // Verificar se a notificação é para este utilizador
            if (payload.uid && userId && payload.uid !== userId) {
                return;
            }

            const paymentIds = payload.payment_ids || [];
            if (!paymentIds.length) {
                return;
            }

            const claimKey = payload.popup_uuid || id || `p:${paymentIds.join(',')}`;

            // Verificar se já processamos esta notificação específica
            if (claimKey && processedNotifications.has(claimKey)) {
                return; // Já processada, ignorar
            }

            // Dar prioridade à aba com focus (aba ativa do utilizador)
            // Abas em background esperam 100ms antes de tentar reclamar
            if (!document.hasFocus()) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }

            // --- Coordenação entre abas: tentar reclamar o popup ---
            if (!(await tryClaimPopup(claimKey))) {
                return; // Outra aba já reclamou, não abrir aqui
            }

            // Evitar duplicados apenas para popup simples.
            const hasOpenPopup = paymentIds.some(pid => openPopups.has(pid));
            if (hasOpenPopup && paymentIds.length === 1) {
                return;
            }

            // Marcar como processada
            if (claimKey) {
                processedNotifications.add(claimKey);
                setTimeout(() => {
                    processedNotifications.delete(claimKey);
                }, 30000);
            }

            // Marcar pagamentos como tendo popup aberto
            paymentIds.forEach(pid => openPopups.add(pid));

            // Validar infraestrutura antes de tentar abrir popup
            if (!busService || !orm || !action) {
                console.warn('[TotalPay] Serviços não disponíveis! Contacte o administrador do sistema.');
                alert('⚠️ Serviço de notificações não está ativo.\n\nNginx e WebSocket são necessários para popups em tempo real.\n\nPor favor, contacte o administrador de sistema.');
                paymentIds.forEach(pid => openPopups.delete(pid));
                return;
            }

            try {
                const actionData = await orm.call(
                    "account.payment",
                    "action_open_totalpay_popup",
                    [paymentIds],
                    {}
                );
                if (actionData) {
                    await action.doAction(actionData);
                    setTimeout(() => {
                        paymentIds.forEach(pid => openPopups.delete(pid));
                    }, 300000);
                }
            } catch (error) {
                console.error('[TotalPay Popup] Erro ao abrir popup:', error);
                paymentIds.forEach(pid => openPopups.delete(pid));
            }
        });

        // Listener registrado e ativo
    },
};

registry.category("services").add("totalpay_popup_listener", totalpayPopupListener);