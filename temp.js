// --- DOM Elements ---
const chatHistory = document.getElementById('chat-history');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const thinkCheckbox = document.getElementById('think-checkbox'); // Restored

// --- WebSocket & Client ID ---
let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0; // Simple counter for unique IDs per turn

// --- State Machine Modes ---
const MODE_ANSWER = 'MODE_ANSWER';
const MODE_SEEKING_CODE_START_FENCE = 'MODE_SEEKING_CODE_START_FENCE'; // Looking for ```
const MODE_SEEKING_CODE_LANGUAGE = 'MODE_SEEKING_CODE_LANGUAGE';     // Reading language after ```
const MODE_INSIDE_CODE_BLOCK = 'MODE_INSIDE_CODE_BLOCK';        // Inside the code block
const MODE_SEEKING_CODE_END_FENCE = 'MODE_SEEKING_CODE_END_FENCE';   // Found potential closing ```

// --- State Variables ---
let currentProcessingMode = MODE_ANSWER;
let fenceBuffer = '';             // Buffer for detecting ``` fences
let langBuffer = '';              // Buffer for language string
let codeBuffer = '';              // Buffer for code content within a block
let currentCodeBlockLang = '';    // Detected language for the current block
let currentCodeBlockElement = null; // The <code> element being filled
let currentCodeBlockPreElement = null; // The <pre> element wrapping the code
let currentAiTurnContainer = null;  // The main container for the AI bubble + its code blocks
let currentAnswerElement = null;    // The div.ai-message bubble
let currentCodeBlocksArea = null; // The div below bubble holding code blocks
let codeBlockCounterThisTurn = 0; // Counter for code blocks within the *current* AI turn
let thinkingRequestedForCurrentTurn = false; // Restored
let isFirstContentChunkForTurn = true;

// --- Constants ---
const FENCE = '```';

// --- Helper Functions ---

function scrollToBottom() {
    // Adding a small delay can sometimes help ensure scrolling happens after render
    setTimeout(() => {
        chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: 'smooth' });
    }, 50);
}

function addUserMessage(text) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'user-message');
    messageElement.textContent = text;
    chatHistory.appendChild(messageElement);
    scrollToBottom();
}

function addSystemMessage(text) {
     const messageElement = document.createElement('div');
     messageElement.classList.add('system-message');
     messageElement.textContent = text;
     chatHistory.appendChild(messageElement);
     scrollToBottom();
}

function addErrorMessage(text) {
     console.error("[UI ERROR] ", text);
     const messageElement = document.createElement('div');
     messageElement.classList.add('error-message'); // Use error styling
     messageElement.textContent = `Error: ${text}`;
     if(currentAiTurnContainer) {
         currentAiTurnContainer.appendChild(messageElement); // Append error within the turn container
     } else {
         chatHistory.appendChild(messageElement); // Fallback if no turn container exists
     }
     scrollToBottom();
}

function setInputDisabledState(disabled) {
    messageInput.disabled = disabled;
    sendButton.disabled = disabled;
    if (thinkCheckbox) {
        thinkCheckbox.disabled = disabled;
    }
}

function finalizeTurnOnErrorOrClose() {
    console.log("[DEBUG] finalizeTurnOnErrorOrClose called.");
    if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK && currentCodeBlockElement) {
        console.warn("Stream ended unexpectedly inside a code block.");
        try {
            Prism.highlightElement(currentCodeBlockElement);
        } catch (e) { console.error("Prism highlighting error on incomplete block:", e); }
    }
    resetStreamingState();
    setInputDisabledState(true); // Disable input on error/close
}

function resetStreamingState() {
    console.log("[DEBUG] Resetting streaming state.");
    currentProcessingMode = MODE_ANSWER;
    fenceBuffer = '';
    langBuffer = '';
    codeBuffer = '';
    currentCodeBlockLang = '';
    currentCodeBlockElement = null;
    currentCodeBlockPreElement = null;
    // Note: Turn-specific elements (currentAiTurnContainer, etc.) are managed by setupNewAiTurn
}

function setupNewAiTurn() {
    currentTurnId++;
    codeBlockCounterThisTurn = 0; // Reset code block counter
    isFirstContentChunkForTurn = true; // <<< Reset the flag for the new turn

    currentAiTurnContainer = document.createElement('div');
    currentAiTurnContainer.classList.add('ai-turn-container');
    currentAiTurnContainer.dataset.turnId = currentTurnId;

    currentAnswerElement = document.createElement('div');
    currentAnswerElement.classList.add('message', 'ai-message');
    currentAiTurnContainer.appendChild(currentAnswerElement);

    currentCodeBlocksArea = document.createElement('div');
    currentCodeBlocksArea.classList.add('code-blocks-area');
    currentAiTurnContainer.appendChild(currentCodeBlocksArea);

    chatHistory.appendChild(currentAiTurnContainer);
}

function appendToAnswerBubble(text) {
    if (!currentAnswerElement) {
        console.error("Attempted to append to null answer bubble!");
        // Attempt recovery only if there's actual text content
        if (text.trim().length === 0) return; // Don't try to recover for whitespace only

        if (currentAiTurnContainer && !currentAiTurnContainer.querySelector('.ai-message')) {
             currentAnswerElement = document.createElement('div');
             currentAnswerElement.classList.add('message', 'ai-message');
             currentAiTurnContainer.insertBefore(currentAnswerElement, currentCodeBlocksArea);
        } else if (currentAiTurnContainer) {
             currentAnswerElement = currentAiTurnContainer.querySelector('.ai-message');
             if(!currentAnswerElement) {
                console.error("CRITICAL: Cannot find or create answer bubble in turn container.");
                return;
             }
        } else {
            console.error("CRITICAL: No turn container to recover answer bubble.");
            return;
        }
    }

    // <<< START: Added Leading Whitespace Trim Logic >>>
    let processedText = text;
    if (isFirstContentChunkForTurn) {
        processedText = text.trimStart(); // Remove leading whitespace ONLY for the first chunk
        // Only set flag to false if we actually processed non-empty text after trimming
        if (processedText.length > 0) {
             isFirstContentChunkForTurn = false;
        }
    }
    // <<< END: Added Leading Whitespace Trim Logic >>>

    // Append text content only if there is something left after trimming
    if (processedText.length > 0) {
        currentAnswerElement.appendChild(document.createTextNode(processedText));
    }
}


function appendCodeReference() {
    if (!currentAnswerElement) {
        console.error("Attempted to append code reference to null answer bubble!");
        return;
    }
    codeBlockCounterThisTurn++;
    const refSpan = document.createElement('span');
    refSpan.classList.add('code-reference');
    refSpan.textContent = `[Code ${codeBlockCounterThisTurn}]`;
    currentAnswerElement.appendChild(refSpan); // Append the span node
}

function createCodeBlockStructure(language) {
    if (!currentCodeBlocksArea) {
        console.error("Attempted to create code block in null area!");
        return;
    }

    const blockId = `code-block-turn${currentTurnId}-${codeBlockCounterThisTurn}`;
    // Normalize language: lowercase, trim, default 'plain', handle potential null/undefined
    const safeLanguage = (language || '').trim().toLowerCase() || 'plain';
    // Alias common variations
    const langAlias = {
        'python': 'python', 'py': 'python',
        'javascript': 'javascript', 'js': 'javascript',
        'html': 'markup', 'xml': 'markup', 'svg': 'markup', // Prism uses 'markup' for HTML/XML
        'css': 'css',
        'bash': 'bash', 'shell': 'bash',
        'java': 'java',
        'csharp': 'csharp', 'cs': 'csharp',
        'cpp': 'cpp', 'c++': 'cpp',
        'ruby': 'ruby', 'rb': 'ruby',
        'go': 'go',
        'php': 'php',
        'json': 'json',
        'yaml': 'yaml', 'yml': 'yaml',
        'sql': 'sql',
        'markdown': 'markdown', 'md': 'markdown'
    };
    const prismLang = langAlias[safeLanguage] || safeLanguage; // Use alias or original safe name
    const displayLang = safeLanguage; // Show the user what they typed (or 'plain')


    const container = document.createElement('div');
    container.classList.add('code-block-container');
    container.id = blockId;

    const header = document.createElement('div');
    header.classList.add('code-block-header');

    const title = document.createElement('span');
    title.textContent = `Code ${codeBlockCounterThisTurn} (${displayLang})`;

    const buttonsDiv = document.createElement('div');
    const toggleBtn = document.createElement('button');
    toggleBtn.classList.add('toggle-code-btn');
    toggleBtn.textContent = 'Show'; // Default text
    const copyBtn = document.createElement('button');
    copyBtn.classList.add('copy-code-btn');
    copyBtn.textContent = 'Copy';

    buttonsDiv.appendChild(toggleBtn);
    buttonsDiv.appendChild(copyBtn);

    // Append buttons first, then title
    header.appendChild(buttonsDiv);
    header.appendChild(title);

    // Create pre/code elements but store references locally for listeners
    const preElement = document.createElement('pre');
    const codeElement = document.createElement('code');
    codeElement.classList.add(`language-${prismLang}`);
    preElement.classList.add('hidden'); // Add hidden class by default

    // Assign references for streaming
    currentCodeBlockPreElement = preElement;
    currentCodeBlockElement = codeElement; // This global ref is used *during* streaming

    preElement.appendChild(codeElement);
    container.appendChild(header);
    container.appendChild(preElement);

    // --- Event Listeners ---

    toggleBtn.addEventListener('click', () => {
        // Find the pre element relative to the button clicked
        const containerDiv = toggleBtn.closest('.code-block-container');
        const preToToggle = containerDiv?.querySelector('pre');
        if (preToToggle) {
            const isHidden = preToToggle.classList.toggle('hidden');
            toggleBtn.textContent = isHidden ? 'Show' : 'Hide'; // Toggle text
        }
    });

    copyBtn.addEventListener('click', () => {
        // <<< START: Updated Copy Logic >>>
        // Find the specific container and code element relative to *this* button
        const containerDiv = copyBtn.closest('.code-block-container');
        const codeElementToCopy = containerDiv?.querySelector('pre > code');

        if (!codeElementToCopy) {
            console.error('Could not find code element to copy for button:', copyBtn);
            copyBtn.textContent = 'Error!';
             setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
            return; // Exit if element not found
        }

        const codeContent = codeElementToCopy.textContent;
        navigator.clipboard.writeText(codeContent).then(() => {
            copyBtn.textContent = 'Copied!';
            copyBtn.classList.add('copied'); // Add class for potential styling feedback
            setTimeout(() => {
                // Check if the text is still 'Copied!' before resetting
                // (prevents flicker if user clicks multiple times quickly)
                if (copyBtn.textContent === 'Copied!') {
                    copyBtn.textContent = 'Copy';
                    copyBtn.classList.remove('copied');
                }
            }, 1500); // Reset after 1.5 seconds
        }).catch(err => {
            console.error('Failed to copy code: ', err);
            copyBtn.textContent = 'Error!';
             // Remove copied class if it was somehow added
            copyBtn.classList.remove('copied');
            setTimeout(() => {
                 if (copyBtn.textContent === 'Error!') {
                    copyBtn.textContent = 'Copy';
                 }
            }, 1500); // Reset after 1.5 seconds
        });
        // <<< END: Updated Copy Logic >>>
    });

    currentCodeBlocksArea.appendChild(container);
}

function appendToCodeBlock(text) {
    if (currentCodeBlockElement) {
        // Append text content to the <code> element
         currentCodeBlockElement.appendChild(document.createTextNode(text));
    } else {
        console.error("Attempted to append to null code block element!");
    }
}

function finalizeCodeBlock() {
    if (currentCodeBlockElement) {
        try {
            // Highlight the completed block using Prism
            Prism.highlightElement(currentCodeBlockElement);
            console.log(`[DEBUG] Highlighted code block ${codeBlockCounterThisTurn} (lang: ${currentCodeBlockLang})`);
            // Optional: Add line numbers if plugin is active
            // if (currentCodeBlockPreElement.classList.contains('line-numbers')) {
            //     Prism.plugins.lineNumbers.resize(currentCodeBlockPreElement);
            // }
        } catch (e) {
            console.error(`Prism highlighting error for lang '${currentCodeBlockLang}':`, e);
            // Add a class indicating error?
             currentCodeBlockElement.classList.add('prism-highlight-error');
        }
    }
    // Reset code block specific state
    currentCodeBlockElement = null;
    currentCodeBlockPreElement = null;
    currentCodeBlockLang = '';
    codeBuffer = ''; // Clear code buffer
}

// --- WebSocket Connection ---
function connectWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${clientId}`;
    console.log(`[DEBUG] Attempting to connect to WebSocket: ${wsUrl}`);

    try {
        const ws = new WebSocket(wsUrl);

        ws.onopen = (event) => {
            console.log("[DEBUG] WebSocket connection opened");
            websocket = ws;
            setInputDisabledState(false); // Enable inputs
            addSystemMessage("Connected to the chat server.");
            // Add initial AI message
            setupNewAiTurn();
            appendToAnswerBubble("AI: Hello! How can I help you today?");
            resetStreamingState(); // Reset state machine after initial message

            if(messageInput.offsetParent !== null) messageInput.focus();
        };

        ws.onmessage = (event) => {
            let chunk = event.data;

            // --- Handle Control Messages First ---
            if (chunk === "<EOS>") {
                console.log("[DEBUG] Received <EOS>. Finalizing turn.");
                 // If ended inside a code block, finalize it
                 if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK) {
                    finalizeCodeBlock();
                } else if (currentProcessingMode === MODE_SEEKING_CODE_END_FENCE && fenceBuffer.length > 0) {
                    // Ended while seeking end fence, treat buffer as regular text
                    appendToAnswerBubble(fenceBuffer);
                 } else if (currentProcessingMode === MODE_SEEKING_CODE_LANGUAGE && langBuffer.length > 0) {
                     // Ended while seeking language, treat as regular text
                    appendToAnswerBubble(FENCE + langBuffer);
                 } else if (currentProcessingMode === MODE_SEEKING_CODE_START_FENCE && fenceBuffer.length > 0) {
                     // Ended while seeking start fence, treat as regular text
                    appendToAnswerBubble(fenceBuffer);
                 }

                resetStreamingState();
                setInputDisabledState(false); // Re-enable inputs
                if(messageInput.offsetParent !== null) messageInput.focus();
                scrollToBottom();
                return; // End processing for this message
            }
            if (chunk.startsWith("<ERROR>")) {
                const errorMessage = chunk.substring(7);
                console.error("[DEBUG] Received <ERROR>:", errorMessage);
                addErrorMessage(errorMessage);
                finalizeTurnOnErrorOrClose(); // Disables input
                scrollToBottom();
                return; // End processing for this message
            }

            // <<< --- START: Tag Filtering --- >>>
            // Remove <think> and </think> tags and any surrounding whitespace globally
            chunk = chunk.replace(/\s*<think>\s*/g, '').replace(/\s*<\/think>\s*/g, '');
            // If the chunk becomes empty after filtering, skip further processing for this chunk
            if (chunk.length === 0) {
                return;
            }
            // <<< --- END: Tag Filtering --- >>>


            // Ensure AI turn container exists (should be set up by setupNewAiTurn)
             if (!currentAiTurnContainer) {
                 console.error("CRITICAL: No AI turn container set up before message chunk received!");
                 // Attempt recovery only if chunk contains non-whitespace content after filtering
                 if (chunk.trim().length > 0) {
                    setupNewAiTurn();
                 } else {
                    return; // Don't set up turn for empty/whitespace-only chunks
                 }
            }

            // --- State Machine Processing ---
            let currentPos = 0;
            while (currentPos < chunk.length) {
                const char = chunk[currentPos];
                let incrementPos = true;

                switch (currentProcessingMode) {
                    case MODE_ANSWER:
                        if (char === FENCE[0]) {
                            fenceBuffer = char;
                            currentProcessingMode = MODE_SEEKING_CODE_START_FENCE;
                        } else {
                            appendToAnswerBubble(char);
                        }
                        break;

                    case MODE_SEEKING_CODE_START_FENCE:
                        if (char === FENCE[fenceBuffer.length]) {
                            fenceBuffer += char;
                            if (fenceBuffer === FENCE) {
                                console.log("[DEBUG] Found ``` start fence.");
                                fenceBuffer = '';
                                langBuffer = '';
                                currentProcessingMode = MODE_SEEKING_CODE_LANGUAGE;
                            }
                        } else {
                            appendToAnswerBubble(fenceBuffer + char);
                            fenceBuffer = '';
                            currentProcessingMode = MODE_ANSWER;
                        }
                        break;

                    case MODE_SEEKING_CODE_LANGUAGE:
                        if (char === '\n') {
                            console.log(`[DEBUG] Found language line: '${langBuffer}'`);
                            currentCodeBlockLang = langBuffer; // Keep raw lang buffer for createCodeBlockStructure
                            appendCodeReference();
                            createCodeBlockStructure(currentCodeBlockLang); // Handles normalization/aliasing
                            langBuffer = '';
                            codeBuffer = '';
                            currentProcessingMode = MODE_INSIDE_CODE_BLOCK;
                        } else if (langBuffer.length > 50) { // Prevent runaway language buffer
                             console.warn("Language line too long, assuming no language.");
                             appendToAnswerBubble(FENCE + langBuffer + char); // Treat as text
                             langBuffer = '';
                             fenceBuffer = '';
                             currentProcessingMode = MODE_ANSWER;
                        } else {
                            langBuffer += char;
                        }
                        break;

                    case MODE_INSIDE_CODE_BLOCK:
                        if (char === FENCE[0]) {
                            fenceBuffer = char;
                            currentProcessingMode = MODE_SEEKING_CODE_END_FENCE;
                        } else {
                            appendToCodeBlock(char);
                        }
                        break;

                    case MODE_SEEKING_CODE_END_FENCE:
                        if (char === FENCE[fenceBuffer.length]) {
                            fenceBuffer += char;
                            if (fenceBuffer === FENCE) {
                                console.log("[DEBUG] Found ``` end fence.");
                                // Check for immediate newline after fence, common pattern
                                if (currentPos + 1 < chunk.length && chunk[currentPos + 1] === '\n') {
                                     currentPos++; // Consume the newline
                                } else {
                                     // If next char is not newline, maybe check later chunks?
                                     // For simplicity, we'll just finalize here.
                                }
                                finalizeCodeBlock();
                                fenceBuffer = '';
                                currentProcessingMode = MODE_ANSWER;
                            }
                        } else {
                            appendToCodeBlock(fenceBuffer + char);
                            fenceBuffer = '';
                            currentProcessingMode = MODE_INSIDE_CODE_BLOCK;
                        }
                        break;

                    default:
                        console.error("Unknown processing mode:", currentProcessingMode);
                        currentProcessingMode = MODE_ANSWER;
                        incrementPos = false;
                }

                if (incrementPos) {
                    currentPos++;
                }
            } // end while loop over chunk

            scrollToBottom();
        }; // end onmessage

        ws.onerror = (event) => {
             console.error("WebSocket error:", event);
             addErrorMessage("WebSocket connection error. Please try refreshing the page.");
             finalizeTurnOnErrorOrClose();
        };

        ws.onclose = (event) => {
             console.log(`[DEBUG] WebSocket connection closed: Code=${event.code}, Reason='${event.reason}', WasClean=${event.wasClean}`);
             addSystemMessage(`Connection closed. ${event.reason ? event.reason : ''} (Code: ${event.code}). Attempting to reconnect...`);
             finalizeTurnOnErrorOrClose();
             websocket = undefined;
             const reconnectDelay = Math.min(1000 * (2 ** Math.min(8, event.code === 1000 ? 0 : 1)), 30000);
             console.log(`[DEBUG] Attempting reconnect in ${reconnectDelay}ms`);
             setTimeout(connectWebSocket, reconnectDelay);
        };

        console.log("[DEBUG] WebSocket object created and handlers assigned.");

    } catch (error) {
        console.error("[DEBUG] Error creating WebSocket:", error);
        addErrorMessage(`Failed to initialize WebSocket connection: ${error.message}. Check browser console.`);
        finalizeTurnOnErrorOrClose();
    }
}

if (chatForm) {
    chatForm.addEventListener('submit', (event) => {
        event.preventDefault(); // Prevent default form submission

        const userMessage = messageInput.value.trim(); // Get user text

        if (!userMessage) {
            return; // Do nothing if message is empty
        }

        // --- Check WebSocket State BEFORE Attempting Send ---
        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            console.warn("[Submit] WebSocket not ready or not defined. State:", websocket?.readyState);
            addErrorMessage("Not connected to the server. Please wait or refresh.");
            return; // Exit if not connected
        }

        // --- If WebSocket is OPEN, proceed ---
        try {
            // --- Step 1: Display User Message ---
            addUserMessage(userMessage); // Show user's raw message immediately

            // --- Step 2: Determine Thinking State & Prepare Message for Backend ---
            thinkingRequestedForCurrentTurn = thinkCheckbox ? thinkCheckbox.checked : false;
            console.log(`[Submit] Checkbox checked: ${thinkingRequestedForCurrentTurn}.`);

            let messageToSend = userMessage; // Default to the raw user message
            // Ensure NO_THINK_PREFIX is defined globally, e.g., const NO_THINK_PREFIX = "\\no_think";
            if (!thinkingRequestedForCurrentTurn) {
                messageToSend = NO_THINK_PREFIX + userMessage;
                console.log(`[Submit] Prepended '${NO_THINK_PREFIX}'. Prepared message: "${messageToSend.substring(0, 100)}..."`);
            } else {
                console.log(`[Submit] Prepared raw message (thinking requested): "${messageToSend.substring(0, 100)}..."`);
            }

            // --- Step 3: Setup UI for AI Response ---
            setupNewAiTurn(); // Uses thinkingRequestedForCurrentTurn for initial visibility

            // --- Step 4: Attempt to Send Message to WebSocket ---
            console.log(`[Submit] >>> About to call websocket.send(). State: ${websocket?.readyState}`); // Log state right before send

            websocket.send(messageToSend); // *** THE ACTUAL SEND CALL ***

            console.log("[Submit] <<< websocket.send() call completed (no immediate error thrown)."); // Log right after send

            // --- Step 5: Clear Input & Disable ---
            messageInput.value = '';          // Clear the input field
            setInputDisabledState(true); // Disable inputs while waiting for response
            console.log("[Submit] Input cleared and disabled.");

        } catch (sendError) {
            // --- Catch Errors During Send or Subsequent Steps ---
            console.error("[Submit] !!! ERROR during send attempt or post-send steps:", sendError);
            addErrorMessage(`Failed to send message: ${sendError.message}`);
            // Optionally re-enable input if send fails? Or leave disabled?
            // setInputDisabledState(false);
        }

    }); // End of submit handler
} else {
    // Handle case where the form element itself is missing
    console.error("CRITICAL ERROR: Could not find chat form element with ID 'chat-form'. Messages cannot be sent.");
    if (typeof addErrorMessage === 'function') {
        addErrorMessage("Initialization Error: Chat input form not found.");
    } else {
        alert("Initialization Error: Chat input form not found.");
    }
}

// --- Initial Setup ---
setInputDisabledState(true); // Disable inputs until connected
connectWebSocket(); // Start connection (will add initial AI message and enable inputs onopen)