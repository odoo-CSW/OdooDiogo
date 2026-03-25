/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onMounted } from "@odoo/owl";

// Constantes
const MBWAY_TIMEOUT = 240; // 4 minutos em segundos
const STATUS_CHECK_INTERVAL = 5000; // 5 segundos em milissegundos
const STAGE_APROVADO = 1;
const STAGE_PENDENTE = 2;
const STAGE_CANCELADO = 3;
const STAGE_FALHOU = 6;

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        
        if (this.props.resModel === 'mbway.timer.wizard') {
            this.orm = useService("orm");
            this.notification = useService("notification");
            this.action = useService("action");
            this._isDestroyed = false;
            this._paymentId = null;
            this._intervals = [];
            this._wizardId = this.props.resId; // ID único do wizard para identificar este popup
            this._popupRoot = null; // Cache do elemento raiz do popup
            
            // Usar onMounted para garantir que o DOM está renderizado
            onMounted(() => {
                if (!this._isDestroyed) {
                    this.initializeTimer();
                }
            });
        }
    },
    
    /**
     * Obtém o elemento raiz do popup para buscar elementos com escopo local
     */
    _getPopupRoot() {
        if (this._popupRoot) return this._popupRoot;
        
        // Tentar encontrar o elemento raiz do formulário através do rootRef
        if (this.rootRef && this.rootRef.el) {
            this._popupRoot = this.rootRef.el;
            // Adicionar atributo identificador único ao sheet
            const sheet = this._popupRoot.querySelector('.mbway-popup-sheet');
                if (sheet && this._wizardId) {
                    sheet.setAttribute('data-wizard-id', this._wizardId);
                }
            return this._popupRoot;
        }
        
        // Alternativa: buscar o último sheet que ainda não tem wizard ID atribuído
        const allSheets = document.querySelectorAll('.mbway-popup-sheet');
        if (allSheets && allSheets.length > 0) {
            // Procurar um sheet sem data-wizard-id
                    for (let i = allSheets.length - 1; i >= 0; i--) {
                const sheet = allSheets[i];
                if (!sheet.getAttribute('data-wizard-id')) {
                    sheet.setAttribute('data-wizard-id', this._wizardId);
                    this._popupRoot = sheet.closest('form') || sheet.parentElement || sheet;
                    return this._popupRoot;
                }
            }
            
            // Se todos os sheets já têm ID, buscar pelo ID específico
            const sheet = document.querySelector(`.mbway-popup-sheet[data-wizard-id="${this._wizardId}"]`);
            if (sheet) {
                this._popupRoot = sheet.closest('form') || sheet.parentElement || sheet;
                return this._popupRoot;
            }
        }
        
        console.error(`[MB WAY] ERRO: Não foi possível encontrar o root para wizard ${this._wizardId}`);
        return null;
    },
    
    /**
     * Busca um elemento apenas dentro do escopo deste popup usando o wizard ID
     */
    _querySelector(selector) {
        // Se já foi destruído, não tentar buscar elementos
        if (this._isDestroyed) {
            return null;
        }
        
        // Se temos o wizard ID, buscar especificamente dentro deste popup
        if (this._wizardId) {
            const sheet = document.querySelector(`.mbway-popup-sheet[data-wizard-id="${this._wizardId}"]`);
            if (sheet) {
                const element = sheet.querySelector(selector);
                if (element) {
                    return element;
                }
                // Elemento não encontrado no sheet - pode ser um seletor inválido
                // Não logar warning pois pode ser normal durante fechamento
                return null;
            }
            // Sheet não encontrado - popup provavelmente já foi fechado
            // Não logar warning para evitar poluição do console
       }
        
        // Fallback: tentar usar o root cacheado
        const root = this._popupRoot;
        if (root) {
            const element = root.querySelector(selector);
            if (element) {
                return element;
            }
        }
        
        return null;
    },
    
    async initializeTimer() {
        try {
            const wizardId = this.props.resId;
            if (!wizardId) return;
            
            const wizardData = await this.orm.read('mbway.timer.wizard', [wizardId], ['payment_id']);
            if (this._isDestroyed || !wizardData || !wizardData[0] || !wizardData[0].payment_id) return;
            
            this._paymentId = wizardData[0].payment_id[0];

            // Garantir que temos o root antes de iniciar as verificações
            const root = this._getPopupRoot();
            
            if (!root) {
                console.error(`[MB WAY] ERRO: Root não encontrado para wizard ${this._wizardId}. Timer não será iniciado.`);
                return;
            }
            
            // Iniciar verificações
            this.checkInitialStatus();
            this.startMbwayTimer();
            this.startStatusChecker();
            
        } catch (error) {
            console.error('[MB WAY] Erro ao inicializar timer:', error);
        }
    },
    
    willUnmount() {
        // Marcar como destruído PRIMEIRO para parar todos os callbacks
        this._isDestroyed = true;
        
        // Limpar intervalos individuais IMEDIATAMENTE
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
        if (this.statusCheckInterval) {
            clearInterval(this.statusCheckInterval);
            this.statusCheckInterval = null;
        }
        
        // Limpar TODOS os intervalos registrados
        if (this._intervals && this._intervals.length > 0) {
            this._intervals.forEach(intervalId => {
                clearInterval(intervalId);
            });
            this._intervals = [];
        }
        
        // Limpar cache do root
        this._popupRoot = null;
        
        // Popup completamente destruído
        
        super.willUnmount?.(...arguments);
    },
    
    /**
     * Método auxiliar para limpar recursos e fechar o popup de forma segura
     * Garante que TODOS os intervalos são parados e recursos liberados
     */
    _cleanupAndClose(delay = 0) {
        if (this._isDestroyed) {
            return;
        }
        
        // Parar TODOS os intervalos imediatamente
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
        if (this.statusCheckInterval) {
            clearInterval(this.statusCheckInterval);
            this.statusCheckInterval = null;
        }
        
        // Limpar array de intervalos
        if (this._intervals && this._intervals.length > 0) {
            this._intervals.forEach(intervalId => clearInterval(intervalId));
            this._intervals = [];
        }
        
        // Limpeza concluída, fechando popup em breve
        
        // Fechar popup após delay
        const closePopup = () => {
            if (!this._isDestroyed && this.action) {
                this.action.doAction({ type: 'ir.actions.act_window_close' });
            }
        };
        
        if (delay > 0) {
            setTimeout(closePopup, delay);
        } else {
            closePopup();
        }
    },

    async checkInitialStatus() {
        // Verificar status inicial ao abrir o popup
        try {
            if (this._isDestroyed) return;
            
            const wizardId = this.props.resId;
            if (!wizardId) return;

            const wizardData = await this.orm.read('mbway.timer.wizard', [wizardId], ['payment_id']);
            if (this._isDestroyed || !wizardData || !wizardData[0] || !wizardData[0].payment_id) return;

            const paymentId = wizardData[0].payment_id[0];

            // Procurar o Pagamento relacionado
            const connectors = await this.orm.searchRead(
                'x_csw_totalpay',
                [['account_payment_id', '=', paymentId]],
                ['x_studio_stage_id', 'x_name'],
                { limit: 1 }
            );

            if (this._isDestroyed) return;

            if (connectors && connectors.length > 0) {
                const connector = connectors[0];
                const stageId = connector.x_studio_stage_id ? connector.x_studio_stage_id[0] : null;
                
                // Se já está Falhado ao abrir
                if (stageId === STAGE_FALHOU) {
                    this.notification.add(
                        `O pagamento ${connector.x_name} falhou. Verifique a configuração e tente novamente.`,
                        {
                            title: '❌ Pagamento Falhou',
                            type: 'danger',
                        }
                    );
                    
                    // Fechar o popup após 3 segundos
                    this._cleanupAndClose(3000);
                }
            }
        } catch (error) {
            // Erro ignorado
        }
    },

    async startMbwayTimer() {
        // Buscar o connector para calcular o tempo restante REAL
        let timeLeft = MBWAY_TIMEOUT; // Default
        let dateStop = null;
        
        try {
            if (this._isDestroyed) return;
            
            const wizardId = this.props.resId;
            if (wizardId) {
                const wizardData = await this.orm.read('mbway.timer.wizard', [wizardId], ['payment_id']);
                if (this._isDestroyed || !wizardData || !wizardData[0] || !wizardData[0].payment_id) return;
                
                const paymentId = wizardData[0].payment_id[0];
                
                // Buscar o connector para obter o x_studio_date_stop e create_date
                const connectors = await this.orm.searchRead(
                    'x_csw_totalpay',
                    [['account_payment_id', '=', paymentId]],
                    ['x_studio_date_stop', 'create_date'],
                    { limit: 1 }
                );
                
                if (this._isDestroyed) return;
                
                if (connectors && connectors.length > 0) {
                    const connector = connectors[0];
                    
                    if (connector.x_studio_date_stop) {
                        // Calcular tempo restante real baseado no date_stop (mais preciso)
                        dateStop = new Date(connector.x_studio_date_stop + 'Z'); // Z força UTC
                        const now = new Date();
                        const diffSeconds = Math.floor((dateStop - now) / 1000);
                        timeLeft = Math.max(0, diffSeconds);
                    } else if (connector.create_date) {
                        // Fallback: calcular baseado no create_date
                        const createDate = new Date(connector.create_date + 'Z');
                        const now = new Date();
                        const elapsedSeconds = Math.floor((now - createDate) / 1000);
                        timeLeft = Math.max(0, MBWAY_TIMEOUT - elapsedSeconds);
                    }
                }
            }
        } catch (error) {
            // Usar tempo default
        }
        
        const updateTimer = () => {
            if (this._isDestroyed) {
                clearInterval(this.timerInterval);
                return;
            }
            
            // Se temos dateStop, recalcular o tempo real a cada tick para precisão
            if (dateStop) {
                const now = new Date();
                const diffSeconds = Math.floor((dateStop - now) / 1000);
                timeLeft = Math.max(0, diffSeconds);
            } else {
                // Fallback: decrementar (menos preciso)
                if (timeLeft > 0) {
                    timeLeft--;
                }
            }
            
            const minutes = Math.floor(timeLeft / 60);
            const seconds = timeLeft % 60;
            
            const display = (minutes < 10 ? '0' : '') + minutes + ':' + 
                           (seconds < 10 ? '0' : '') + seconds;
            
            const timerElement = this._querySelector('.mbway_timer_display');
            if (timerElement) {
                timerElement.textContent = display;
            }
            
            if (timeLeft <= 0) {
                if (this._timeoutHandled) {
                    return;
                }
                this._timeoutHandled = true;

                clearInterval(this.timerInterval);
                clearInterval(this.statusCheckInterval);
                
                if (timerElement) {
                    timerElement.textContent = '00:00';
                }
                
                if (this._isDestroyed) return;
                
                // Chamar método para marcar como falhado - mas apenas se ainda não foi destruído
                const wizardId = this.props.resId;
                if (wizardId && this.orm && !this._isDestroyed) {
                    let closeScheduled = false;
                    const scheduleClose = () => {
                        if (closeScheduled || this._isDestroyed) return;
                        closeScheduled = true;
                        this._cleanupAndClose(2000);
                    };

                    // Garantir fechamento mesmo se a chamada não resolver
                    setTimeout(scheduleClose, 6000);

                    this.orm.call(
                        'mbway.timer.wizard',
                        'action_timeout',
                        [wizardId]
                    ).then((result) => {
                        if (this._isDestroyed) return;

                        if (result && this.action && !this._timeoutNotified) {
                            this._timeoutNotified = true;
                            this.action.doAction(result);
                        } else if (!result && this.notification && !this._timeoutNotified) {
                            this._timeoutNotified = true;
                            this.notification.add(
                                'O prazo de 4 minutos expirou. O pagamento sera marcado como falhado.',
                                {
                                    title: '❌ Tempo Esgotado',
                                    type: 'danger',
                                }
                            );
                        }
                        
                        // O método Python já retorna uma notificação que é processada automaticamente
                        // Não precisamos mostrar notificação aqui para evitar duplicação
                        
                        // Fechar o popup após 2 segundos, apenas se ainda não foi destruído
                        scheduleClose();
                    }).catch((error) => {
                        // Se foi destruído, ignorar erro silenciosamente
                        if (this._isDestroyed) return;
                        
                        // Apenas logar erro se foi um problema real
                        if (error.message !== 'Component is destroyed') {
                            console.error('[MB WAY] Erro ao chamar action_timeout:', error);
                        }
                        
                        if (this.notification && !this._timeoutNotified) {
                            this._timeoutNotified = true;
                            this.notification.add(
                                'O prazo de 4 minutos expirou. O pagamento sera marcado como falhado.',
                                {
                                    title: '❌ Tempo Esgotado',
                                    type: 'danger',
                                }
                            );
                        }

                        // Fechar o popup apenas se ainda não foi destruído
                        scheduleClose();
                    });
                } else if (!this._isDestroyed) {
                    // Fallback se não conseguir chamar o método
                    if (this.notification) {
                        this.notification.add(
                            'O prazo de 4 minutos expirou. Verifique manualmente o status do pagamento.',
                            {
                                title: 'Tempo Esgotado',
                                type: 'warning',
                            }
                        );
                    }
                    
                    if (!this._isDestroyed) {
                        this._cleanupAndClose(2000);
                    }
                }
            }
        };
        
        // Atualizar imediatamente e depois a cada segundo
        updateTimer();
        this.timerInterval = setInterval(() => updateTimer(), 1000);
        this._intervals.push(this.timerInterval); // Registrar para limpeza
    },

    startStatusChecker() {
        const checkPaymentStatus = async () => {
            try {
                if (this._isDestroyed) return;
                
                // Buscar o payment_id do registro atual
                const wizardId = this.props.resId;
                if (!wizardId) return;

                const wizardData = await this.orm.read('mbway.timer.wizard', [wizardId], ['payment_id']);
                if (this._isDestroyed || !wizardData || !wizardData[0] || !wizardData[0].payment_id) return;

                const paymentId = wizardData[0].payment_id[0];

                // Buscar o CSW Connector relacionado para verificar estado atual
                const connectors = await this.orm.searchRead(
                    'x_csw_totalpay',
                    [['account_payment_id', '=', paymentId]],
                    ['x_studio_stage_id', 'x_name', 'x_studio_error_message'],
                    { limit: 1 }
                );

                if (this._isDestroyed) return;

                if (connectors && connectors.length > 0) {
                    const connector = connectors[0];
                    const stageId = connector.x_studio_stage_id ? connector.x_studio_stage_id[0] : null;
                    const errorMessage = connector.x_studio_error_message || '';
                    
                    if (this._isDestroyed) return;
                    // Processar estado atual
                    // Aprovado
                    if (stageId === STAGE_APROVADO) {
                        clearInterval(this.statusCheckInterval);
                        clearInterval(this.timerInterval);
                        
                        if (this._isDestroyed) return;
                        
                        // Atualizar imagem no popup para sucesso
                        const imgElement = this._querySelector('.mbway_status_image');
                        if (imgElement) {
                            imgElement.src = '/totalpay/static/img/check.png';
                            imgElement.alt = 'Pagamento Aprovado';
                            imgElement.style.maxWidth = '100px';
                        }
                        
                        // Atualizar título
                        const titleElement = this._querySelector('.mbway_status_title');
                        if (titleElement) {
                            titleElement.innerHTML = '✅ Pagamento Aprovado!';
                            titleElement.style.color = '#28a745';
                        }
                        
                        // Atualizar mensagem e esconder cronômetro
                        const messageElement = this._querySelector('.mbway_status_message');
                        if (messageElement) {
                            messageElement.innerHTML = '<span class="fa fa-check-circle" style="font-size: 16px; margin-right: 5px; color: #28a745;"></span>Pagamento efetuado com sucesso!';
                            messageElement.style.color = '#28a745';
                            messageElement.style.fontSize = '16px';
                            messageElement.style.fontWeight = 'bold';
                        }
                        
                        // Mudar cor do container
                        const containerElement = this._querySelector('.mbway_timer_container');
                        if (containerElement) {
                            containerElement.style.background = 'linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%)';
                        }
                        
                        // Esconder o cronômetro
                        const timerDisplay = this._querySelector('.mbway_timer_display');
                        if (timerDisplay && timerDisplay.parentElement) {
                            timerDisplay.parentElement.style.display = 'none';
                        }
                        
                        if (!this._isDestroyed && this.notification) {
                            this.notification.add(
                                `O pagamento ${connector.x_name} foi efetuado com sucesso via MB WAY.`,
                                {
                                    title: '✅ Pagamento Aprovado!',
                                    type: 'success',
                                }
                            );
                        }
                        
                        if (!this._isDestroyed) {
                            this._cleanupAndClose(3000);
                        }
                    }
                    // Falhou
                    else if (stageId === STAGE_FALHOU) {
                        clearInterval(this.statusCheckInterval);
                        clearInterval(this.timerInterval);
                        
                        if (this._isDestroyed) return;
                        // Atualizar imagem no popup para erro
                        const imgElement = this._querySelector('.mbway_status_image');
                        if (imgElement) {
                            imgElement.src = '/totalpay/static/img/close.png';
                            imgElement.alt = 'Pagamento Recusado';
                            imgElement.style.maxWidth = '100px';
                        }
                        
                        // Atualizar título
                        const titleElement = this._querySelector('.mbway_status_title');
                        if (titleElement) {
                            titleElement.innerHTML = 'Pagamento Recusado';
                            titleElement.style.color = '#dc3545';
                        }
                        
                        // Atualizar mensagem
                        const messageElement = this._querySelector('.mbway_status_message');
                        if (messageElement) {
                            const message = 'O pagamento não foi aprovado. Por favor, tente novamente.';
                            messageElement.innerHTML = `<span class="fa fa-times-circle" style="font-size: 16px; margin-right: 5px; color: #dc3545;"></span>${message}`;
                            messageElement.style.color = '#dc3545';
                            messageElement.style.fontSize = '14px';
                        }
                        
                        // Mudar cor do container
                        const containerElement = this._querySelector('.mbway_timer_container');
                        if (containerElement) {
                            containerElement.style.background = 'linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%)';
                        }
                        
                        // Esconder o cronômetro
                        const timerDisplay = this._querySelector('.mbway_timer_display');
                        if (timerDisplay && timerDisplay.parentElement) {
                            timerDisplay.parentElement.style.display = 'none';
                        }
                        
                        if (!this._isDestroyed && this.notification) {
                            this.notification.add(
                                'O pagamento foi recusado. Por favor, tente novamente.',
                                {
                                    title: 'Pagamento Recusado',
                                    type: 'danger',
                                }
                            );
                        }
                        
                        if (!this._isDestroyed) {
                            this._cleanupAndClose(4000);
                        }
                    }
                    // Cancelado
                    else if (stageId === STAGE_CANCELADO) {
                        clearInterval(this.statusCheckInterval);
                        clearInterval(this.timerInterval);
                        
                        if (this._isDestroyed) return;
                        
                        // Atualizar imagem no popup para cancelado
                        const imgElement = this._querySelector('.mbway_status_image');
                        if (imgElement) {
                            imgElement.src = '/totalpay/static/img/close.png';
                            imgElement.alt = 'Pagamento Cancelado';
                            imgElement.style.maxWidth = '100px';
                        }
                        
                        // Atualizar título
                        const titleElement = this._querySelector('.mbway_status_title');
                        if (titleElement) {
                            titleElement.innerHTML = '⚠️ Pagamento Cancelado';
                            titleElement.style.color = '#ffc107';
                        }
                        
                        // Atualizar mensagem
                        const messageElement = this._querySelector('.mbway_status_message');
                        if (messageElement) {
                            const message = 'O pagamento foi cancelado.';
                            messageElement.innerHTML = `<span class="fa fa-exclamation-triangle" style="font-size: 16px; margin-right: 5px; color: #ffc107;"></span>${message}`;
                            messageElement.style.color = '#856404';
                            messageElement.style.fontSize = '14px';
                        }
                        
                        // Mudar cor do container
                        const containerElement = this._querySelector('.mbway_timer_container');
                        if (containerElement) {
                            containerElement.style.background = 'linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%)';
                        }
                        
                        // Esconder o cronômetro
                        const timerDisplay = this._querySelector('.mbway_timer_display');
                        if (timerDisplay && timerDisplay.parentElement) {
                            timerDisplay.parentElement.style.display = 'none';
                        }
                        
                        if (!this._isDestroyed && this.notification) {
                            this.notification.add(
                                'O pagamento foi cancelado.',
                                {
                                    title: '⚠️ Pagamento Cancelado',
                                    type: 'warning',
                                }
                            );
                        }
                        
                        if (!this._isDestroyed) {
                            this._cleanupAndClose(3000);
                        }
                    }
                }
            } catch (error) {
                // Erro ignorado - verificação silenciosa
            }
        };

        // Iniciar verificação periódica
        this.statusCheckInterval = setInterval(() => checkPaymentStatus(), STATUS_CHECK_INTERVAL);
        this._intervals.push(this.statusCheckInterval); // Registrar para limpeza
    },
});
