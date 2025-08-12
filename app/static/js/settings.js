// app/static/js/settings.js
import { loadSidebarHTML, populateSessionList } from '/static/js/app-ui.js';

/**
 * Retrieves a cookie value by its name.
 * @param {string} name The name of the cookie to retrieve.
 * @returns {string|null} The cookie value, or null if not found.
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            // Does this cookie string begin with the name we want?
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

document.addEventListener('DOMContentLoaded', async () => {
    console.log("Settings Page: DOMContentLoaded.");

    // Ensure sidebar is scrollable using the class defined in input.css
    const sidebarLoaderTarget = document.getElementById('sidebar-loader-target');
    if (sidebarLoaderTarget) {
        sidebarLoaderTarget.classList.add('sidebar-scrollable');
    }

    const sidebarLoaded = await loadSidebarHTML('/static/_sidebar.html', 'sidebar-loader-target');
    if (sidebarLoaded) {
        await populateSessionList('/api/sessions', 'session-list', '/chat/');
    } else {
        console.error("Settings Page: Sidebar loading failed.");
    }

    const settingsMessageArea = document.getElementById('settings-message-area');

    /**
     * Displays a message in the settings message area and focuses it.
     * @param {string} message The message to display.
     * @param {string} type The type of message ('info', 'success', 'error').
     * @param {number} duration Time in ms to display the message (0 for indefinite).
     * @param {HTMLElement|null} elementToFocusAfter An optional element to return focus to after message timeout.
     */
    function showSettingsMessage(message, type = 'info', duration = 0, elementToFocusAfter = null) {
        settingsMessageArea.textContent = message;
        settingsMessageArea.className = 'py-3 px-4 rounded-md text-center mb-4 text-sm'; // Reset classes
        if (type === 'success') {
            settingsMessageArea.classList.add('bg-green-100', 'text-green-800', 'border', 'border-green-300');
        } else if (type === 'error') {
            settingsMessageArea.classList.add('bg-red-100', 'text-red-800', 'border', 'border-red-300');
        } else { // 'info' or default
            settingsMessageArea.classList.add('bg-blue-100', 'text-blue-700', 'border', 'border-blue-300');
        }
        settingsMessageArea.style.display = 'block';

        settingsMessageArea.setAttribute('tabindex', '-1');
        settingsMessageArea.focus();

        if (duration > 0) {
            setTimeout(() => {
                settingsMessageArea.style.display = 'none';
                settingsMessageArea.removeAttribute('tabindex');
                if (elementToFocusAfter && typeof elementToFocusAfter.focus === 'function') {
                    elementToFocusAfter.focus();
                }
            }, duration);
        }
    }

    const currentNameInput = document.getElementById('current-name');
    const currentEmailInput = document.getElementById('current-email');

    async function fetchCurrentUser() {
        try {
            const response = await fetch('/api/me', { cache: 'no-store' });
            if (response.ok) {
                const userData = await response.json();
                if (currentNameInput) currentNameInput.value = userData.name;
                if (currentEmailInput) currentEmailInput.value = userData.email;
                window.currentUserDetails = userData;
            } else {
                showSettingsMessage('Could not load your current user details.', 'error', 0, document.body);
                if (currentNameInput) currentNameInput.value = 'Error loading';
                if (currentEmailInput) currentEmailInput.value = 'Error loading';
            }
        } catch (error) {
            console.error("Error fetching current user:", error);
            showSettingsMessage('An error occurred while loading your details.', 'error', 0, document.body);
        }
    }
    await fetchCurrentUser();

    // --- Account Settings Forms (Keep existing logic for these) ---
    const updateNameForm = document.getElementById('update-name-form');
    if (updateNameForm) {
        const newNameInput = document.getElementById('new-name');
        updateNameForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            settingsMessageArea.style.display = 'none';
            const updateNameButton = document.getElementById('update-name-button');
            updateNameButton.disabled = true;
            updateNameButton.textContent = 'Updating...';
            const newName = newNameInput.value;
            const currentPasswordForName = document.getElementById('current-password-for-name');
            const currentPassword = currentPasswordForName.value;
            // Use window.csrfTokenRaw which should be populated by the server
            const csrfToken = window.csrfTokenRaw; 
            let focusTargetAfterMessage = newNameInput;

            if (!csrfToken || csrfToken === "%%CSRF_TOKEN_RAW%%") {
                showSettingsMessage('CSRF token missing. Cannot update name. Please refresh.', 'error', 0, updateNameButton);
                updateNameButton.disabled = false;
                updateNameButton.textContent = 'Update Name';
                return;
            }

            try {
                const response = await fetch('/api/me/update-name', {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    body: JSON.stringify({ new_name: newName, current_password: currentPassword })
                });
                const result = await response.json();
                if (response.ok) {
                    showSettingsMessage(result.message || 'Name updated successfully!', 'success', 3000, newNameInput);
                    if (currentNameInput) currentNameInput.value = result.new_name;
                    currentPasswordForName.value = '';
                    newNameInput.value = '';
                } else {
                    focusTargetAfterMessage = result.field === 'current_password' ? currentPasswordForName : newNameInput;
                    showSettingsMessage(result.detail || 'Failed to update name.', 'error', 0, focusTargetAfterMessage);
                }
            } catch (error) {
                console.error("Error updating name:", error);
                showSettingsMessage('An error occurred. Please try again.', 'error', 0, newNameInput);
            } finally {
                updateNameButton.disabled = false;
                updateNameButton.textContent = 'Update Name';
            }
        });
    }

    const updateEmailForm = document.getElementById('update-email-form');
    if (updateEmailForm) {
        const newEmailInput = document.getElementById('new-email');
        updateEmailForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            settingsMessageArea.style.display = 'none';
            const updateEmailButton = document.getElementById('update-email-button');
            updateEmailButton.disabled = true;
            updateEmailButton.textContent = 'Updating Email...';
            const newEmail = newEmailInput.value;
            const currentPasswordForEmail = document.getElementById('current-password-for-email');
            const currentPassword = currentPasswordForEmail.value;
            const csrfToken = window.csrfTokenRaw; // Use window.csrfTokenRaw
            let focusTargetAfterMessage = newEmailInput;

            if (!csrfToken || csrfToken === "%%CSRF_TOKEN_RAW%%") {
                showSettingsMessage('CSRF token missing. Cannot update email. Please refresh.', 'error', 0, updateEmailButton);
                updateEmailButton.disabled = false;
                updateEmailButton.textContent = 'Update Email Address';
                return;
            }

            try {
                const response = await fetch('/api/me/update-email', {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    body: JSON.stringify({ new_email: newEmail, current_password: currentPassword })
                });
                const result = await response.json();
                if (response.ok) {
                    showSettingsMessage(result.message || 'Email updated. Logging out...', 'success', 0);
                    setTimeout(() => { window.location.href = '/logout'; }, 4000);
                } else {
                    focusTargetAfterMessage = result.field === 'current_password' ? currentPasswordForEmail : newEmailInput;
                    showSettingsMessage(result.detail || 'Failed to update email.', 'error', 0, focusTargetAfterMessage);
                    updateEmailButton.disabled = false;
                    updateEmailButton.textContent = 'Update Email Address';
                }
            } catch (error) {
                console.error("Error updating email:", error);
                showSettingsMessage('An error occurred. Please try again.', 'error', 0, newEmailInput);
                updateEmailButton.disabled = false;
                updateEmailButton.textContent = 'Update Email Address';
            }
        });
    }

    const regeneratePasswordForm = document.getElementById('regenerate-password-form');
    if (regeneratePasswordForm) {
        const currentPasswordForRegen = document.getElementById('current-password-for-regen');
        regeneratePasswordForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            settingsMessageArea.style.display = 'none';
            const regeneratePasswordButton = document.getElementById('regenerate-password-button');
            regeneratePasswordButton.disabled = true;
            regeneratePasswordButton.textContent = 'Regenerating...';
            const currentPassword = currentPasswordForRegen.value;
            const csrfToken = window.csrfTokenRaw; // Use window.csrfTokenRaw

            if (!csrfToken || csrfToken === "%%CSRF_TOKEN_RAW%%") {
                showSettingsMessage('CSRF token missing. Cannot regenerate password. Please refresh.', 'error', 0, regeneratePasswordButton);
                regeneratePasswordButton.disabled = false;
                regeneratePasswordButton.textContent = 'Regenerate Password & Send Email';
                return;
            }

            try {
                const response = await fetch('/api/me/regenerate-password', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': csrfToken
                    },
                    body: JSON.stringify({ current_password: currentPassword })
                });
                const result = await response.json();
                if (response.ok) {
                    showSettingsMessage(result.message || 'Password regenerated. Check email. Logging out...', 'success', 0);
                    setTimeout(() => { window.location.href = '/logout'; }, 4000);
                } else {
                    showSettingsMessage(result.detail || 'Failed to regenerate password.', 'error', 0, currentPasswordForRegen);
                    regeneratePasswordButton.disabled = false;
                    regeneratePasswordButton.textContent = 'Regenerate Password & Send Email';
                }
            } catch (error) {
                console.error("Error regenerating password:", error);
                showSettingsMessage('An error occurred. Please try again.', 'error', 0, currentPasswordForRegen);
                regeneratePasswordButton.disabled = false;
                regeneratePasswordButton.textContent = 'Regenerate Password & Send Email';
            }
        });
    }

    // --- LLM Settings Functionality ---
    const llmProviderSelect = document.getElementById('llm-provider');
    const llmModelSelect = document.getElementById('llm-model');
    const llmApiKeyGroup = document.getElementById('llm-api-key-group');
    const llmApiKeyInput = document.getElementById('llm-api-key');
    const llmApiKeyStatus = document.getElementById('llm-api-key-status');
    const llmBaseUrlGroup = document.getElementById('llm-base-url-group');
    const llmBaseUrlInput = document.getElementById('llm-base-url');
    const llmProviderStatus = document.getElementById('llm-provider-status');
    const saveLLMSettingsButton = document.getElementById('save-llm-settings-button');
    const llmSettingsForm = document.getElementById('llm-settings-form');

    let availableProvidersData = []; // To store fetched provider details, including their models

    /**
     * Loads initial LLM configuration data: available providers and current user's LLM settings.
     * Populates the provider dropdown and sets initial values for other fields.
     */
    async function loadLLMConfigData() {
        if (!llmProviderSelect || !llmModelSelect) {
            console.error("LLM configuration select elements not found in the DOM.");
            return;
        }
        try {
            // Fetch all available providers and their details (including models)
            const providersResponse = await fetch('/api/llm/providers');
            if (!providersResponse.ok) {
                showSettingsMessage('Failed to load LLM providers configuration.', 'error', 0, llmProviderSelect);
                llmProviderSelect.innerHTML = '<option value="">Error loading providers</option>';
                llmModelSelect.innerHTML = '<option value="">-- Select Provider First --</option>';
                llmModelSelect.disabled = true;
                return;
            }
            availableProvidersData = await providersResponse.json(); // Store for later use

            // Fetch the current user's saved LLM settings
            const userSettingsResponse = await fetch('/api/me/llm-settings', { cache: 'no-store' });
            const currentUserLLMSettings = userSettingsResponse.ok ? await userSettingsResponse.json() : {};
            
            if (!userSettingsResponse.ok && availableProvidersData.length > 0) {
                showSettingsMessage('Could not load your saved LLM settings. Defaults may be shown.', 'error', 4000, llmProviderSelect);
            }

            // Populate the providers dropdown
            populateLLMProviders(currentUserLLMSettings.selected_llm_provider_id);

            // Set the initially selected provider (if any)
            const effectiveProviderId = currentUserLLMSettings.selected_llm_provider_id || "";
            if (llmProviderSelect.querySelector(`option[value="${effectiveProviderId}"]`)) {
                 llmProviderSelect.value = effectiveProviderId;
            } else {
                llmProviderSelect.value = ""; // Default to "Select Provider"
            }
           

            // Update model dropdown and other fields based on the (potentially) selected provider
            updateModelDropdown(llmProviderSelect.value, currentUserLLMSettings.selected_llm_model_id, currentUserLLMSettings);
            updateProviderSpecificFields(llmProviderSelect.value, currentUserLLMSettings);

        } catch (error) {
            console.error("Error loading LLM configuration:", error);
            showSettingsMessage('A critical error occurred while loading LLM configuration.', 'error', 0, llmProviderSelect);
            llmProviderSelect.innerHTML = '<option value="">Error</option>';
            llmModelSelect.innerHTML = '<option value="">Error</option>';
            llmModelSelect.disabled = true;
        }
    }

    /**
     * Populates the LLM Provider dropdown.
     * @param {string|null} currentProviderId The ID of the currently selected provider (to pre-select it).
     */
    function populateLLMProviders(currentProviderId) {
        llmProviderSelect.innerHTML = '<option value="">-- Select Provider --</option>'; // Default empty option
        availableProvidersData.forEach(provider => {
            // You might have filtering logic here if needed, e.g., based on provider.is_system_configured
            const option = document.createElement('option');
            option.value = provider.id;
            option.textContent = provider.display_name;
            if (provider.id === currentProviderId) {
                option.selected = true;
            }
            llmProviderSelect.appendChild(option);
        });
    }

    /**
     * Updates the LLM Model dropdown based on the selected provider.
     * Enables or disables the model dropdown accordingly.
     * @param {string} providerId The ID of the selected provider.
     * @param {string|null} currentModelId The ID of the currently selected model (to pre-select it).
     * @param {object} userSettings The current user's LLM settings (for context).
     */
    function updateModelDropdown(providerId, currentModelId, userSettings = {}) {
        llmModelSelect.innerHTML = '<option value="">-- Select Model --</option>'; // Default empty option
        const selectedProvider = availableProvidersData.find(p => p.id === providerId);

        if (selectedProvider && selectedProvider.available_models && selectedProvider.available_models.length > 0) {
            llmModelSelect.disabled = false; // *** ENABLE the dropdown ***
            selectedProvider.available_models.forEach(model => {
                const option = document.createElement('option');
                option.value = model.model_id;
                // Display both name and ID for clarity, especially if names are not unique across providers
                option.textContent = model.display_name ? `${model.display_name} (${model.model_id})` : model.model_id;
                
                // Pre-select if this model was the user's saved choice for this provider
                if (providerId === userSettings.selected_llm_provider_id && model.model_id === currentModelId) {
                    option.selected = true;
                }
                llmModelSelect.appendChild(option);
            });
        } else if (providerId) { // Provider selected, but no models
            llmModelSelect.innerHTML = '<option value="">No models available for this provider</option>';
            llmModelSelect.disabled = true; // *** DISABLE if no models ***
        } else { // No provider selected
            llmModelSelect.innerHTML = '<option value="">-- Select Provider First --</option>';
            llmModelSelect.disabled = true; // *** DISABLE if no provider selected ***
        }
    }

    /**
     * Updates provider-specific fields (API key, base URL) based on the selected provider.
     * @param {string} providerId The ID of the selected provider.
     * @param {object} userSettings The current user's LLM settings.
     */
    function updateProviderSpecificFields(providerId, userSettings = {}) {
        const selectedProvider = availableProvidersData.find(p => p.id === providerId);

        // Hide all optional groups by default
        llmApiKeyGroup.style.display = 'none';
        llmApiKeyGroup.setAttribute('aria-hidden', 'true');
        llmBaseUrlGroup.style.display = 'none';
        llmBaseUrlGroup.setAttribute('aria-hidden', 'true');

        // Reset fields and status messages
        llmProviderStatus.textContent = providerId ? "Loading provider details..." : "Select a provider to see more options.";
        llmApiKeyInput.value = '';
        llmApiKeyInput.placeholder = '';
        llmApiKeyStatus.textContent = '';
        llmBaseUrlInput.value = '';
        llmBaseUrlInput.placeholder = '';

        if (selectedProvider) {
            // API Key field visibility and status
            if (selectedProvider.can_accept_user_api_key) { // Check if provider *can* accept a user key
                llmApiKeyGroup.style.display = 'block';
                llmApiKeyGroup.setAttribute('aria-hidden', 'false');
                if (userSettings.has_user_api_key && selectedProvider.id === userSettings.selected_llm_provider_id) {
                    llmApiKeyInput.placeholder = "•••••••• (A key is currently saved)";
                    llmApiKeyStatus.textContent = "A key is saved. Edit to change, or clear field and save to remove your key.";
                } else if (selectedProvider.is_system_configured && selectedProvider.needs_api_key_from_user === false) {
                    // System has a key, and user doesn't strictly need to provide one, but can.
                    llmApiKeyInput.placeholder = "API Key (Optional - System key available)";
                    llmApiKeyStatus.textContent = "A system key is configured. You can provide your own to override it for your account.";
                } else if (selectedProvider.needs_api_key_from_user === true) {
                    llmApiKeyInput.placeholder = "API Key (Required)";
                    llmApiKeyStatus.textContent = "Please enter your API key for this provider.";
                } else { // Can accept, but not strictly required and no system key (e.g. some local models)
                    llmApiKeyInput.placeholder = "API Key (Optional)";
                    llmApiKeyStatus.textContent = "You can optionally provide an API key.";
                }
            }

            // Base URL field visibility and status
            if (selectedProvider.can_accept_user_base_url) { // Check if provider *can* accept a user base URL
                llmBaseUrlGroup.style.display = 'block';
                llmBaseUrlGroup.setAttribute('aria-hidden', 'false');
                if (selectedProvider.id === userSettings.selected_llm_provider_id && userSettings.selected_llm_base_url) {
                    llmBaseUrlInput.value = userSettings.selected_llm_base_url;
                }
                // Provide more specific placeholders based on provider type if available
                if (selectedProvider.type === 'ollama') {
                    llmBaseUrlInput.placeholder = "e.g., http://localhost:11434 (uses system default if empty)";
                } else if (selectedProvider.type === 'openai_compatible_server') {
                    llmBaseUrlInput.placeholder = "e.g., https://api.example.com/v1 (Required if no system default)";
                } else {
                    llmBaseUrlInput.placeholder = "Custom Base URL (Optional)";
                }
            }

            // General provider status message
            if (!selectedProvider.is_system_configured && selectedProvider.needs_api_key_from_user === true) {
                llmProviderStatus.textContent = "This provider requires configuration (e.g., an API key).";
            } else if (selectedProvider.is_system_configured && selectedProvider.needs_api_key_from_user === false) {
                llmProviderStatus.textContent = "This provider is system-configured and ready to use.";
            } else if (selectedProvider.can_accept_user_api_key || selectedProvider.can_accept_user_base_url) {
                llmProviderStatus.textContent = "This provider can be customized with your own API key or base URL.";
            } else {
                llmProviderStatus.textContent = "This provider does not require additional configuration.";
            }

        } else if (providerId === "") { // "-- Select Provider --" is chosen
             llmProviderStatus.textContent = "Select a provider to see configuration options.";
        }
    }

    // Event listener for when the LLM provider selection changes
    llmProviderSelect.addEventListener('change', async (event) => {
        const selectedProviderId = event.target.value;
        
        // Fetch the latest user settings as they might have changed or to get defaults for the new provider
        const userSettingsResponse = await fetch('/api/me/llm-settings', {cache: 'no-store'});
        const currentUserLLMSettings = userSettingsResponse.ok ? await userSettingsResponse.json() : {};

        // Determine which model should be pre-selected.
        // If the newly selected provider is the same as the user's saved provider, use their saved model.
        // Otherwise, no specific model is pre-selected (will default to "-- Select Model --").
        const modelToSelect = (selectedProviderId === currentUserLLMSettings.selected_llm_provider_id)
                                ? currentUserLLMSettings.selected_llm_model_id
                                : null; // Let updateModelDropdown handle the default "-- Select Model --"

        updateModelDropdown(selectedProviderId, modelToSelect, currentUserLLMSettings);
        updateProviderSpecificFields(selectedProviderId, currentUserLLMSettings);
    });

    // Event listener for saving LLM settings
    llmSettingsForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        settingsMessageArea.style.display = 'none';
        saveLLMSettingsButton.disabled = true;
        saveLLMSettingsButton.textContent = 'Saving...';
        const csrfToken = window.csrfTokenRaw; // Use the globally available raw CSRF token

        if (!csrfToken || csrfToken === "%%CSRF_TOKEN_RAW%%") {
            showSettingsMessage('CSRF token missing. Cannot save settings. Please refresh.', 'error', 0, saveLLMSettingsButton);
            saveLLMSettingsButton.disabled = false;
            saveLLMSettingsButton.textContent = 'Save LLM Settings';
            return;
        }

        const providerId = llmProviderSelect.value;
        const modelId = llmModelSelect.value;
        // Only include API key if the input group is visible (meaning provider accepts it)
        const apiKey = (llmApiKeyGroup.style.display !== 'none') ? llmApiKeyInput.value : undefined;
        // Only include base URL if the input group is visible
        const baseUrl = (llmBaseUrlGroup.style.display !== 'none') ? llmBaseUrlInput.value : undefined;

        const payload = {
            selected_llm_provider_id: providerId || null, // Send null if empty string
            selected_llm_model_id: modelId || null,     // Send null if empty string
        };

        // Conditionally add api_key and base_url to the payload
        // The backend expects `user_llm_api_key` and `selected_llm_base_url`
        if (apiKey !== undefined) { // Check for undefined, empty string means "clear the key"
            payload.user_llm_api_key = apiKey;
        }
        if (baseUrl !== undefined) { // Check for undefined, empty string means "clear the base URL"
            payload.selected_llm_base_url = baseUrl || null; // Send null if empty string
        }

        try {
            const response = await fetch('/api/me/llm-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken
                },
                body: JSON.stringify(payload)
            });
            const result = await response.json();
            if (response.ok) {
                showSettingsMessage('LLM settings saved successfully!', 'success', 3000, saveLLMSettingsButton);
                // Reload the config data to reflect saved settings (e.g., has_user_api_key status)
                await loadLLMConfigData(); 
            } else {
                let fieldToFocus = llmProviderSelect;
                if (result.detail && typeof result.detail === 'string') {
                    if (result.detail.toLowerCase().includes('model')) fieldToFocus = llmModelSelect;
                    else if (result.detail.toLowerCase().includes('api key')) fieldToFocus = llmApiKeyInput;
                    else if (result.detail.toLowerCase().includes('base url')) fieldToFocus = llmBaseUrlInput;
                }
                showSettingsMessage(result.detail || 'Failed to save LLM settings.', 'error', 0, fieldToFocus);
            }
        } catch (error) {
            console.error("Error saving LLM settings:", error);
            showSettingsMessage('An error occurred while saving LLM settings.', 'error', 0, saveLLMSettingsButton);
        } finally {
            saveLLMSettingsButton.disabled = false;
            saveLLMSettingsButton.textContent = 'Save LLM Settings';
        }
    });

    // Initial load of LLM configuration when the page is ready
    await loadLLMConfigData(); 
});
