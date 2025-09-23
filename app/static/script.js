// --- JS Imports ---
import { marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';
import { updateAndDisplayParticipants } from './js/session-manager.js';

// --- Prism Components ---
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-csharp';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-docker';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';

// --- DOM Elements ---
const chatHistory = document.getElementById('chat-history');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const thinkCheckbox = document.getElementById('think-checkbox');
const stopAiButton = document.getElementById('stop-ai-button');




const LANGUAGE_ALIASES = {
    'python': 'python', 'py': 'python',
    'javascript': 'javascript', 'js': 'javascript',
    'html': 'html',
    'css': 'css',
    'bash': 'bash', 'sh': 'bash', 'shell': 'bash',
    'json': 'json',
    'c': 'c',
    'cpp': 'cpp', 'c++': 'cpp',
    'csharp': 'csharp', 'cs': 'csharp',
    'go': 'go',
    'rust': 'rust',
    'typescript': 'typescript', 'ts': 'typescript',
    'java': 'java',
    'plaintext': 'plaintext', 'text': 'plaintext',
};

const PRISM_LANGUAGE_MAP = {
    'html': 'markup', // Prism's class for HTML is 'markup'
    'xml': 'markup',
    'svg': 'markup',
    'c++': 'cpp',
    'cs': 'csharp',
    'js': 'javascript',
    'py': 'python',
    'ts': 'typescript',
    'sh': 'bash',
    'shell': 'bash'
};

// --- State Variables ---
let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0;
let projectDataCache = {};
let currentAiTurnContainer = null;
let currentAnswerElement = null;
let currentCodeBlocksArea = null;
let streamingCodeBlockCounter = 0;
let currentStreamingAnswerElement = null;
let currentStreamingFile = {
    container: null,
    codeElement: null,
    path: null
};


function escapeHTML(str) {
    if (str === null || str === undefined) return '';
    return str.toString()
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function getCursorPosition(parentElement) {
    const selection = window.getSelection();
    if (selection.rangeCount === 0) return -1;

    const range = selection.getRangeAt(0);
    if (!parentElement.contains(range.startContainer)) {
        return -1;
    }

    const preSelectionRange = range.cloneRange();
    preSelectionRange.selectNodeContents(parentElement);
    try {
        preSelectionRange.setEnd(range.startContainer, range.startOffset);
        return preSelectionRange.toString().length;
    } catch (e) {
        console.error("Error getting cursor position:", e);
        return -1;
    }
}

function setCursorPosition(parentElement, offset) {
    const selection = window.getSelection();
    if (!selection) return;

    const range = document.createRange();
    let charCount = 0;
    let foundStart = false;

    function findNodeAndOffset(node) {
        if (foundStart) return;

        if (node.nodeType === Node.TEXT_NODE) {
            const nextCharCount = charCount + node.length;
            if (!foundStart && offset >= charCount && offset <= nextCharCount) {
                try {
                    const offsetInNode = Math.min(offset - charCount, node.length);
                    range.setStart(node, offsetInNode);
                    foundStart = true;
                } catch (e) {
                    console.error("Error setting range start:", e);
                }
            }
            charCount = nextCharCount;
        } else {
            for (let i = 0; i < node.childNodes.length; i++) {
                findNodeAndOffset(node.childNodes[i]);
                if (foundStart) break;
            }
        }
    }

    findNodeAndOffset(parentElement);

    if (foundStart) {
        range.collapse(true);
        selection.removeAllRanges();
        selection.addRange(range);
    } else {
        range.selectNodeContents(parentElement);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
    }
}

function initializeCodeBlockHistory(blockId, initialContent) {
    if (!window.codeBlockHistories) {
        window.codeBlockHistories = new Map();
    }
    
    if (!window.codeBlockHistories.has(blockId)) {
        window.codeBlockHistories.set(blockId, {
            history: [initialContent],
            currentIndex: 0,
            saveTimeout: null
        });
    }
}

async function initializeCurrentUser() {
    try {
        const response = await fetch('/api/me');
        if (response.ok) {
            const userData = await response.json();
            if (userData && userData.name) {
                window.currentUserInfo = {
                    name: userData.name,
                    email: userData.email,
                    id: userData.id
                };
            } else {
                window.currentUserInfo = null;
            }
        } else {
            window.currentUserInfo = null;
        }
    } catch (error) {
        window.currentUserInfo = null;
    }
}

function getSessionIdFromPath() {
    const pathName = window.location.pathname;
    const pathParts = pathName.split('/');

    if (pathParts.length >= 3 && pathParts[1] === 'chat') {
        const sessionId = pathParts[2];
        if (sessionId && sessionId.trim() !== "") {
            return sessionId;
        } else {
            console.error("Session ID extracted from path is empty or invalid.");
            return null;
        }
    }

    return null;
}

function scrollToBottom(behavior = 'auto') {
    const isNearBottom = chatHistory.scrollHeight - chatHistory.scrollTop - chatHistory.clientHeight < 100;
    if (isNearBottom) {
        requestAnimationFrame(() => {
            chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: behavior });
        });
    }
}

function setInputDisabledState(inputsDisabled, aiResponding) {
    if (messageInput) messageInput.disabled = inputsDisabled;
    if (sendButton) sendButton.disabled = inputsDisabled;
    if (thinkCheckbox) thinkCheckbox.disabled = inputsDisabled;

    if (stopAiButton) {
        if (aiResponding) {
            stopAiButton.classList.remove('hidden');
            stopAiButton.disabled = false;
            stopAiButton.innerHTML = `
                <svg class="w-5 h-5 inline-block mr-1" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1zm4 0a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>
                Stop`;
            if (sendButton) sendButton.classList.add('hidden');
        } else {
            stopAiButton.classList.add('hidden');
            stopAiButton.disabled = true;
            stopAiButton.innerHTML = `
                <svg class="w-5 h-5 inline-block mr-1" fill="currentColor" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8 7a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1zm4 0a1 1 0 00-1 1v4a1 1 0 102 0V8a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>
                Stop`;
            if (sendButton) sendButton.classList.remove('hidden');
        }
    }
}

function handleTypingIndicators(payload) {
    const { user_id, user_name, is_typing, color } = payload;
    const indicatorId = `typing-indicator-${user_id}`;
    let existingIndicator = document.getElementById(indicatorId);

    if (is_typing && !existingIndicator) {
        const bubble = document.createElement('div');
        bubble.id = indicatorId;
        bubble.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'flex', 'flex-col', 'self-start', 'mr-auto');
        bubble.style.backgroundColor = color || '#dbeafe';

        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-gray-800');
        senderElem.textContent = escapeHTML(user_name);

        const dots = document.createElement('span');
        dots.classList.add('loading-dots');

        bubble.appendChild(senderElem);
        bubble.appendChild(dots);
        chatHistory.appendChild(bubble);
        scrollToBottom('smooth');
    } else if (!is_typing && existingIndicator) {
        existingIndicator.remove();
    }
}

function addUserMessage(text) {
    if (!chatHistory) return;

    const messageElement = document.createElement('div');
    messageElement.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'self-end', 'ml-auto');
    
    const currentUser = window.currentUserInfo;
    const participantData = currentUser && window.participantInfo ? window.participantInfo[currentUser.id] : null;

    if (participantData && participantData.color) {
        messageElement.style.backgroundColor = participantData.color;
    } else {
        messageElement.classList.add('bg-gray-200');
    }

    const senderElem = document.createElement('p');
    senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-gray-800');
    senderElem.textContent = escapeHTML(currentUser ? currentUser.name : 'User');
    messageElement.appendChild(senderElem);

    const contentElem = document.createElement('div');
    contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');
    contentElem.textContent = text.replace(/@\w+/g, '').trim();
    messageElement.appendChild(contentElem);

    chatHistory.appendChild(messageElement);
    setTimeout(() => scrollToBottom('smooth'), 50);
}

function updateParticipantListUI(active_user_id = null) {
    const participants = document.querySelectorAll('#participant-list li[id^="participant-"]');
    participants.forEach(p => {
        const nameSpan = p.querySelector('.participant-name-span');
        if (nameSpan) {
            const oldIndicator = nameSpan.querySelector('.ai-indicator');
            if(oldIndicator) oldIndicator.remove();

            if (p.id === `participant-${active_user_id}`) {
                const indicator = document.createElement('span');
                indicator.className = 'ai-indicator text-xs text-gray-500 font-semibold ml-1';
                indicator.textContent = '(+AI)';
                nameSpan.appendChild(indicator);
            }
        }
    });
}



function handleEndAnswerStream(payload) {
    if (currentStreamingAnswerElement && currentStreamingAnswerElement.dataset.rawContent) {
        renderMarkdownAndKatex(currentStreamingAnswerElement.dataset.rawContent, currentStreamingAnswerElement);
    }
    currentStreamingAnswerElement = null;
    updateParticipantListUI(null);
}

function handleEndFileStream(payload) {
    if (currentStreamingFile && currentStreamingFile.codeElement) {
        Prism.highlightElement(currentStreamingFile.codeElement);
    }
    currentStreamingFile = { container: null, codeElement: null, path: null, fileData: null };
    
    const turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${payload.turn_id}']`);
    const fileBlocks = turnContainer ? turnContainer.querySelectorAll('.block-container') : [];
    const projectData = projectDataCache[payload.turn_id];

    if (projectData && fileBlocks.length === projectData.files.length) {
         updateParticipantListUI(null);
    }
}

function finalizeTurnOnErrorOrClose() {
    console.log("[STREAM] Finalizing turn.");
    setInputDisabledState(false, false);
    if (messageInput) messageInput.focus();

    currentStreamingAnswerElement = null;
    currentStreamingFile = {
        container: null,
        codeElement: null,
        path: null
    };
    updateParticipantListUI(null);
}

function addSystemMessage(text) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('system-message');
    messageElement.textContent = text;
    chatHistory.appendChild(messageElement);
    setTimeout(() => scrollToBottom('smooth'), 50);
}

function addErrorMessage(text) {
    console.error("[UI ERROR] ", text);
    const messageElement = document.createElement('div');
    messageElement.classList.add('error-message');
    messageElement.textContent = `Error: ${text}`;
    if (currentAiTurnContainer) {
        const target = currentAnswerElement || currentCodeBlocksArea || currentAiTurnContainer;
        target.appendChild(messageElement);
    } else {
        chatHistory.appendChild(messageElement);
    }
    setTimeout(() => scrollToBottom('smooth'), 50);
}

function renderMarkdownAndKatex(contentString, targetElement) {
    if (typeof contentString !== 'string' || !targetElement) {
        if (targetElement) targetElement.innerHTML = "";
        return;
    }

    const storedKatex = {};
    let katexPlaceholderIndex = 0;
    
    // --- CHANGE START: Updated Regex to include \[...\] and \(...\) ---
    const katexRegexGlobal = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]|(?<!\\)\$((?:\\\$|[^$])+?)(?<!\\)\$|(?<!\\)\\\(([\s\S]+?)(?<!\\)\\\)/g;

    let textForMarkdownParsing = contentString.replace(katexRegexGlobal, (match, displayDollars, displayBrackets, inlineDollars, inlineParens) => {
        // --- CHANGE START: Updated logic to handle new capture groups ---
        const isDisplayMode = !!(displayDollars || displayBrackets);
        const katexString = (displayDollars || displayBrackets || inlineDollars || inlineParens).trim();
        // --- CHANGE END ---

        const cleanedKatexString = katexString.replace(/\\([$])/g, '$1');
        let katexHtml = '';
        try {
            katexHtml = katex.renderToString(cleanedKatexString, {
                displayMode: isDisplayMode, throwOnError: false, output: "html", strict: false
            });
        } catch (e) {
            katexHtml = `<span class="katex-error" title="${escapeHTML(e.toString())}">${escapeHTML(match)}</span>`;
        }
        
        const placeholderId = `MPLD3KATEXPLACEHOLDER${katexPlaceholderIndex++}`;

        storedKatex[placeholderId] = katexHtml;
        return placeholderId;
    });

    let html = marked.parse(textForMarkdownParsing);

    for (const placeholderId in storedKatex) {
        const regex = new RegExp(placeholderId, "g");
        html = html.replace(regex, storedKatex[placeholderId]);
    }

    targetElement.innerHTML = html;
}

function createCodeBlock(language, codeContent, originalCodeForDataset, turnIdSuffix, codeBlockIndex, codeBlocksAreaElement, isRunnable = false, promptingUserId = null) {
    if (!codeBlocksAreaElement) {
        console.error("createCodeBlock: Code blocks area element is null!");
        return;
    }
    const rawLang = (language || 'plaintext').trim().toLowerCase();
    const canonicalLang = LANGUAGE_ALIASES[rawLang] || 'plaintext';
    const prismLang = PRISM_LANGUAGE_MAP[canonicalLang] || canonicalLang;
    const blockId = `code-block-turn${turnIdSuffix}-${codeBlockIndex}`;
    const container = document.createElement('div');
    container.classList.add('block-container');
    container.id = blockId;
    container.dataset.language = canonicalLang;
    container.dataset.originalContent = originalCodeForDataset;
    const codeHeader = document.createElement('div');
    codeHeader.classList.add('block-header');
    if (promptingUserId !== null) {
        const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
        const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
        codeHeader.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
    }
    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('block-buttons');
    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'block-action-btn');
    runStopBtn.dataset.status = 'idle';
    runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
    runStopBtn.title = 'Run Project';
    runStopBtn.addEventListener('click', handleRunStopCodeClick);
    if (!isRunnable) {
        runStopBtn.style.display = 'none';
    }
    codeButtonsDiv.appendChild(runStopBtn);
    const restoreBtn = document.createElement('button');
    restoreBtn.classList.add('restore-code-btn', 'block-action-btn');
    restoreBtn.textContent = 'Restore';
    restoreBtn.title = 'Restore Original Code';
    const toggleCodeBtn = document.createElement('button');
    toggleCodeBtn.classList.add('toggle-code-btn', 'block-action-btn');
    toggleCodeBtn.textContent = 'Hide';
    toggleCodeBtn.title = 'Show/Hide Code';
    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'block-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';
    codeButtonsDiv.appendChild(restoreBtn);
    codeButtonsDiv.appendChild(toggleCodeBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);
    const codeTitle = document.createElement('span');
    codeTitle.classList.add('block-title');
    const titleTextSpan = document.createElement('span');
    titleTextSpan.classList.add('title-text');
    titleTextSpan.textContent = `Code Block ${codeBlockIndex} (${canonicalLang})`;
    codeTitle.appendChild(titleTextSpan);
    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);
    const preElement = document.createElement('pre');
    preElement.classList.add('manual');
    const codeElement = document.createElement('code');
    codeElement.className = `language-${prismLang}`;
    codeElement.setAttribute('contenteditable', 'true');
    codeElement.setAttribute('spellcheck', 'false');
    codeElement.textContent = codeContent;
    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);
    codeBlocksAreaElement.appendChild(container);
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try { Prism.highlightElement(codeElement); } catch (e) { console.error(`Prism highlight error:`, e); }
    }
    toggleCodeBtn.addEventListener('click', () => { const isHidden = preElement.classList.toggle('hidden'); toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide'; });
    copyCodeBtn.addEventListener('click', async () => { try { await navigator.clipboard.writeText(codeElement.textContent || ''); copyCodeBtn.textContent = 'Copied!'; setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500); } catch (err) { console.error('Failed to copy code: ', err); } });
    restoreBtn.addEventListener('click', async () => { const originalContent = container.dataset.originalContent; const sessionId = getSessionIdFromPath(); if (!sessionId || !window.csrfTokenRaw) return; try { const response = await fetch(`/api/sessions/${sessionId}/edited-blocks/${blockId}`, { method: 'DELETE', headers: { 'X-CSRF-Token': window.csrfTokenRaw } }); if (response.ok) { const cursorPos = getCursorPosition(codeElement); codeElement.textContent = originalContent; if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') { try { Prism.highlightElement(codeElement); setCursorPosition(codeElement, Math.min(cursorPos, originalContent.length)); } catch (e) { console.error(`Prism highlight error:`, e); } } saveCodeBlockState(blockId, originalContent); } else { const error = await response.json(); alert(`Failed to restore code block: ${error.detail || 'Server error'}`); } } catch (error) { console.error("Error restoring code block:", error); alert("An error occurred while restoring the code block."); } });
    codeElement.addEventListener('keydown', (event) => { handleCodeBlockKeydown(event, blockId); });
    codeElement.addEventListener('blur', () => { const content = codeElement.textContent || ''; saveCodeBlockContent(blockId, content); });
    codeElement.addEventListener('input', () => { const cursorPos = getCursorPosition(codeElement); const content = codeElement.textContent || ''; setTimeout(() => { if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') { try { Prism.highlightElement(codeElement); setCursorPosition(codeElement, cursorPos); } catch (e) { console.error(`Prism highlight error:`, e); } } }, 10); saveCodeBlockState(blockId, content); });
    return container;
}

function renderPlotWhenReady(plotDivId, plotData, timeout = 3000) {
    const startTime = Date.now();
    const interval = setInterval(() => {
        if (typeof mpld3 !== 'undefined' && typeof mpld3.draw_figure === 'function') {
            clearInterval(interval);
            
            try {
                // Let mpld3 handle the initial drawing.
                mpld3.draw_figure(plotDivId, plotData);

                // --- NEW LOGIC START: Make the generated SVG responsive ---
                const plotDiv = document.getElementById(plotDivId);
                if (plotDiv) {
                    const svg = plotDiv.querySelector('svg');
                    if (svg) {
                        const originalWidth = svg.getAttribute('width');
                        const originalHeight = svg.getAttribute('height');

                        if (originalWidth && originalHeight) {
                            // Set the viewBox to the original dimensions
                            svg.setAttribute('viewBox', `0 0 ${originalWidth} ${originalHeight}`);
                            // Ensure aspect ratio is maintained
                            svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                            
                            // Remove fixed dimensions and use CSS for responsive scaling
                            svg.removeAttribute('width');
                            svg.removeAttribute('height');
                            svg.style.width = '100%';
                            svg.style.height = 'auto';
                        }
                    }
                }
                // --- NEW LOGIC END ---

            } catch (error) {
                console.error('Error during mpld3.draw_figure:', error);
                const plotDiv = document.getElementById(plotDivId);
                if (plotDiv) {
                    plotDiv.textContent = "Error rendering plot. See browser console for details.";
                }
            }

        } else if (Date.now() - startTime > timeout) {
            clearInterval(interval);
            const plotDiv = document.getElementById(plotDivId);
            if (plotDiv) {
                plotDiv.textContent = "Error: Timed out waiting for mpld3.js library to load.";
            }
            console.error("Timed out waiting for mpld3 to become available.");
        }
    }, 50);
}

function handleStructuredMessage(messageData) {
    const { type, payload } = messageData;
    if (!payload || !payload.project_id) return;

    const projectId = payload.project_id;
    const codeContainer = document.getElementById(projectId);
    const outputBlockId = `output-for-${projectId}`;
    let outputContainer = document.getElementById(outputBlockId);

    // This function is now simplified, as createOrClearOutputContainer handles creation
    if (!outputContainer) {
        // If an output message arrives but there's no container, create it.
        outputContainer = createOrClearOutputContainer(projectId);
    }
    
    switch (type) {
        case 'code_output': {
            const outputPre = outputContainer.querySelector('.block-output-console pre');
            if (outputPre) {
                addCodeOutput(outputPre, payload.stream, payload.data);
                outputContainer.style.display = ''; // Ensure it's visible
            }
            break;
        }
        case 'code_finished': {
            const { exit_code, error } = payload;
            let finishMessage = `Finished (Exit: ${exit_code})`;
            let statusClass = (exit_code === 0) ? 'success' : 'error';
            if (error) {
                finishMessage = 'Failed';
                statusClass = 'error';
                const outputPre = outputContainer.querySelector('.block-output-console pre');
                if (outputPre) addCodeOutput(outputPre, 'stderr', `Error: ${error}`);
            }
            
            updateHeaderStatus(outputContainer, finishMessage, statusClass);
            if (codeContainer) {
                const runStopBtn = codeContainer.querySelector('.run-code-btn');
                if (runStopBtn) {
                    runStopBtn.dataset.status = 'idle';
                    runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
                    runStopBtn.title = 'Run Code';
                }
            }
            break;
        }
    }
}

function createOrClearOutputContainer(projectId, title, promptingUserId) {
    const outputBlockId = `output-for-${projectId}`;
    let outputContainer = document.getElementById(outputBlockId);
    const codeContainer = document.getElementById(projectId);
    if (outputContainer) {
        const outputPre = outputContainer.querySelector('.block-output-console pre');
        if (outputPre) outputPre.innerHTML = '';
    } else {
        outputContainer = document.createElement('div');
        outputContainer.id = outputBlockId;
        outputContainer.className = 'block-container';
        const outputHeader = document.createElement('div');
        outputHeader.className = 'block-header';
        outputHeader.innerHTML = createOutputHeaderHTML(title, promptingUserId);
        const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
        const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
        outputHeader.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
        const outputConsoleDiv = document.createElement('div');
        outputConsoleDiv.className = 'block-output-console';
        const outputPre = document.createElement('pre');
        outputConsoleDiv.appendChild(outputPre);
        outputContainer.appendChild(outputHeader);
        outputContainer.appendChild(outputConsoleDiv);
        if (codeContainer) {
            codeContainer.insertAdjacentElement('afterend', outputContainer);
        } else {
            chatHistory.appendChild(outputContainer);
        }
        outputHeader.querySelector('.toggle-output-btn').addEventListener('click', (e) => { const isHidden = outputConsoleDiv.classList.toggle('hidden'); e.target.textContent = isHidden ? 'Show' : 'Hide'; });
        outputHeader.querySelector('.copy-output-btn').addEventListener('click', async (e) => { await navigator.clipboard.writeText(outputPre.textContent || ''); e.target.textContent = 'Copied!'; setTimeout(() => { e.target.textContent = 'Copy'; }, 1500); });
    }
    outputContainer.style.display = '';
    return outputContainer;
}

function createPlotBlock(projectId, plotData, plotIndex) {
    const codeContainer = document.getElementById(projectId);
    if (!codeContainer) return;

    const uniquePlotBlockId = `${projectId}-plot-${plotIndex}`;
    const existingPlot = document.getElementById(uniquePlotBlockId);
    if (existingPlot) existingPlot.remove();

    const outputContainer = createOrClearOutputContainer(projectId);
    
    const plotContainer = document.createElement('div');
    plotContainer.id = uniquePlotBlockId;
    plotContainer.className = 'block-container';

    const plotHeader = document.createElement('div');
    plotHeader.className = 'block-header';
    plotHeader.innerHTML = `
        <div class="block-buttons">
            <button class="toggle-output-btn block-action-btn">Hide</button>
        </div>
        <span class="block-title">Plot Output ${plotIndex}</span>
        <span class="block-status success">Rendered</span>
    `;

    const plotDivId = `plot-div-${uniquePlotBlockId}`;
    const plotDiv = document.createElement('div');
    plotDiv.id = plotDivId;
    plotDiv.className = 'mpld3-plot-container';

    plotContainer.appendChild(plotHeader);
    plotContainer.appendChild(plotDiv);
    outputContainer.insertAdjacentElement('afterend', plotContainer);

    plotHeader.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
        plotContainer.classList.toggle('hidden');
        e.target.textContent = plotContainer.classList.contains('hidden') ? 'Show' : 'Hide';
    });

    renderPlotWhenReady(plotDivId, plotData);
}

function saveCodeBlockContent(blockId, content) {
    const container = document.getElementById(blockId);
    if (!container) return;
    
    const language = container.dataset.language;
    const sessionId = getSessionIdFromPath();
    
    if (!sessionId || !websocket || websocket.readyState !== WebSocket.OPEN) {
        return;
    }
    
    websocket.send(JSON.stringify({
        type: 'save_code_content',
        payload: {
            session_id: sessionId,
            code_block_id: blockId,
            language: language,
            code_content: content
        }
    }));
}

function createOutputHeaderHTML(title, promptingUserId, statusText = 'Running...', statusClass = 'running') {
    const prompterColor = window.participantInfo?.[promptingUserId]?.color || '#dbeafe';
    const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
    const gradient = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
    return `
        <div class="block-buttons">
            <button class="toggle-output-btn block-action-btn">Hide</button>
            <button class="copy-output-btn block-action-btn">Copy</button>
        </div>
        <span class="block-title" style="color: #374151;">Output of ${escapeHTML(title)}</span>
        <span class="block-status ${statusClass}">${statusText}</span>
    `;
}

async function handleRunStopCodeClick(event) {
    const button = event.currentTarget;
    const container = button.closest('.block-container');
    if (!container) return;
    const codeBlockId = container.id;
    const status = button.dataset.status;
    if (status === 'running' || status === 'previewing') {
        if (websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ type: 'stop_code', payload: { project_id: codeBlockId } }));
        }
        return;
    }
    const turnIdMatch = codeBlockId.match(/turn(\d+)/);
    const turnId = turnIdMatch ? parseInt(turnIdMatch[1], 10) : null;
    const projectData = turnId ? projectDataCache[turnId] : null;
    if (!projectData) {
        alert("Error: Could not find project data for this execution.");
        return;
    }
    const updatedProjectData = JSON.parse(JSON.stringify(projectData));
    const turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turnId}']`);
    if (turnContainer) {
        updatedProjectData.files.forEach(file => {
            const allTitleElements = turnContainer.querySelectorAll('.block-title .title-text');
            const codeBlockTitle = Array.from(allTitleElements).find(span => span.textContent === file.path);
            if (codeBlockTitle) {
                const codeElement = codeBlockTitle.closest('.block-container').querySelector('code');
                if (codeElement) {
                    file.content = codeElement.textContent;
                }
            }
        });
    }

    // --- FIX START: Correct language detection ---
    let mainLanguage = null;
    const languagePriority = ['html', 'python', 'javascript', 'cpp', 'c', 'rust', 'go', 'java', 'csharp', 'typescript'];
    for (const lang of languagePriority) {
        if (updatedProjectData.files.some(file => file.language === lang)) {
            mainLanguage = lang;
            break;
        }
    }
    // If no primary language is found, default to bash.
    if (!mainLanguage) {
        mainLanguage = 'bash';
    }
    // --- FIX END ---

    if (websocket && websocket.readyState === WebSocket.OPEN) {
        const titleElement = container.querySelector('.block-title .title-text');
        const title = titleElement ? titleElement.textContent : 'run.sh';
        const promptingUserId = projectData.prompting_user_id || null;
        const outputContainer = createOrClearOutputContainer(codeBlockId, title, promptingUserId);
        updateHeaderStatus(outputContainer, 'Starting...', 'running');
        button.dataset.status = 'running';
        button.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
        button.title = 'Stop Execution';
        websocket.send(JSON.stringify({
            type: 'run_code',
            payload: {
                project_data: updatedProjectData,
                project_id: codeBlockId,
                language: mainLanguage
            }
        }));
    } else {
        addErrorMessage("Cannot run code: Not connected to server.");
    }
}

function updateHeaderStatus(outputContainer, statusText, statusClass) {
    const statusSpan = outputContainer.querySelector('.block-status');
    if (statusSpan) {
        statusSpan.textContent = statusText;
        statusSpan.className = `block-status ${statusClass}`;
    }
    
    const outputHeader = outputContainer.querySelector('.block-header');
    if (outputHeader && outputHeader.classList.contains('header-is-sticky-js')) {
        const stickyId = outputHeader.dataset.stickyId;
        if (stickyId) {
            const stickyClone = document.getElementById(stickyId);
            if (stickyClone) {
                const stickyStatusSpan = stickyClone.querySelector('.block-status');
                if (stickyStatusSpan) {
                    stickyStatusSpan.textContent = statusText;
                    stickyStatusSpan.className = `block-status ${statusClass}`;
                }
            }
        }
    }
}

function addCodeOutput(outputPreElement, streamType, text, language) {
    if (!outputPreElement || !text) return;

    // The language is now passed in directly, so we don't need to guess it from the DOM.
    if (language === 'html' && streamType !== 'stderr') {
        if (!outputPreElement.htmlBuffer) {
            outputPreElement.htmlBuffer = '';
        }
        outputPreElement.htmlBuffer += text;
        return;
    }

    // For all other languages, append the output as text.
    const span = document.createElement('span');
    span.classList.add(streamType === 'stderr' ? 'stderr-output' : 'stdout-output');
    span.textContent = text;
    outputPreElement.appendChild(span);
    outputPreElement.scrollTop = outputPreElement.scrollHeight;
}

function performUndo(blockId) {
    if (!window.codeBlockHistories) return false;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData || historyData.currentIndex <= 0) return false;
    
    historyData.currentIndex--;
    const content = historyData.history[historyData.currentIndex];
    
    const container = document.getElementById(blockId);
    if (!container) return false;
    
    const codeElement = container.querySelector('code');
    if (!codeElement) return false;
    
    const cursorPos = getCursorPosition(codeElement);
    codeElement.textContent = content;
    
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try {
            Prism.highlightElement(codeElement);
            setCursorPosition(codeElement, Math.min(cursorPos, content.length));
        } catch (e) {
            console.error(`Prism highlight error:`, e);
        }
    }
    
    return true;
}

function performRedo(blockId) {
    if (!window.codeBlockHistories) return false;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData || historyData.currentIndex >= historyData.history.length - 1) return false;
    
    historyData.currentIndex++;
    const content = historyData.history[historyData.currentIndex];
    
    const container = document.getElementById(blockId);
    if (!container) return false;
    
    const codeElement = container.querySelector('code');
    if (!codeElement) return false;
    
    const cursorPos = getCursorPosition(codeElement);
    codeElement.textContent = content;
    
    if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
        try {
            Prism.highlightElement(codeElement);
            setCursorPosition(codeElement, Math.min(cursorPos, content.length));
        } catch (e) {
            console.error(`Prism highlight error:`, e);
        }
    }
    
    return true;
}

function handleCodeBlockKeydown(event, blockId) {
    const isCtrlZ = event.ctrlKey && event.key === 'z' && !event.shiftKey;
    const isCtrlY = event.ctrlKey && (event.key === 'y' || (event.key === 'z' && event.shiftKey));
    
    if (isCtrlZ) {
        event.preventDefault();
        performUndo(blockId);
        return;
    }
    
    if (isCtrlY) {
        event.preventDefault();
        performRedo(blockId);
        return;
    }
    
    const codeElement = event.target;
    const content = codeElement.textContent || '';
    saveCodeBlockState(blockId, content);
}

function saveCodeBlockState(blockId, content) {
    if (!window.codeBlockHistories) return;
    
    const historyData = window.codeBlockHistories.get(blockId);
    if (!historyData) return;

    clearTimeout(historyData.saveTimeout);
    historyData.saveTimeout = setTimeout(() => {
        const lastContent = historyData.history[historyData.currentIndex];
        if (lastContent !== content) {
            historyData.history = historyData.history.slice(0, historyData.currentIndex + 1);
            historyData.history.push(content);
            
            if (historyData.history.length > 50) {
                historyData.history.shift();
            } else {
                historyData.currentIndex++;
            }
        }
    }, 2000);
}

function restoreCodeExecutionResult(result) {
    // Only create an output block if there's actual terminal output to show.
    if (!result.output_content) {
        return;
    }

    const codeContainer = document.getElementById(result.code_block_id);
    if (!codeContainer) return;

    // Create or clear the output container for the historical output
    const outputContainer = createOrClearOutputContainer(result.code_block_id);
    const outputPre = outputContainer.querySelector('.block-output-console pre');

    if (outputPre) {
        outputPre.textContent = result.output_content;
    }

    const statusText = result.error_message ? 'Failed' : `Finished (Exit: ${result.exit_code})`;
    const statusClass = result.error_message || result.exit_code !== 0 ? 'error' : 'success';
    updateHeaderStatus(outputContainer, statusText, statusClass);
}

async function loadAndDisplayChatHistory(sessionId) {
    await updateAndDisplayParticipants();

    const chatHistoryDiv = document.getElementById('chat-history');
    if (!chatHistoryDiv) {
        console.error("Chat history container 'chat-history' not found.");
        return;
    }

    chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">Loading history...</p>';

    try {
        const [messagesResponse, codeResultsResponse, editedBlocksResponse] = await Promise.all([
            fetch(`/api/sessions/${sessionId}/messages`),
            fetch(`/api/sessions/${sessionId}/code-results`),
            fetch(`/api/sessions/${sessionId}/edited-blocks`)
        ]);

        if (!messagesResponse.ok) {
            const errorData = await messagesResponse.json().catch(() => ({ detail: "Failed to load chat history." }));
            throw new Error(errorData.detail || messagesResponse.statusText);
        }

        const messages = await messagesResponse.json();
        const codeResults = codeResultsResponse.ok ? await codeResultsResponse.json() : [];
        const editedCodeBlocks = editedBlocksResponse.ok ? await editedBlocksResponse.json() : {};

        chatHistoryDiv.innerHTML = '';

        if (messages.length === 0) {
            chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">No messages in this session yet. Start chatting!</p>';
        } else {
            messages.forEach(msg => {
                renderSingleMessage(msg, chatHistoryDiv, true, editedCodeBlocks);
            });

            codeResults.forEach(result => {
                restoreCodeExecutionResult(result);
            });

            if (typeof Prism !== 'undefined' && typeof Prism.highlightAll === 'function') {
                Prism.highlightAll();
            }

            const allCodeBlocks = chatHistoryDiv.querySelectorAll('.block-container[id^="code-block-turn"]');
            let maxBlockIndex = 0;
            allCodeBlocks.forEach(block => {
                const parts = block.id.split('-');
                const blockIndex = parseInt(parts[parts.length - 1], 10);
                if (!isNaN(blockIndex) && blockIndex > maxBlockIndex) {
                    maxBlockIndex = blockIndex;
                }
            });
            streamingCodeBlockCounter = maxBlockIndex;

            chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight;
        }

    } catch (error){
        console.error(`Failed to fetch or display chat history for session ${sessionId}:`, error);
        chatHistoryDiv.innerHTML = `<p class="text-center text-red-500 p-4">An unexpected error occurred while loading history: ${escapeHTML(error.message)}</p>`;
    }
}

function renderSingleMessage(msg, parentElement, isHistory = false, editedCodeBlocks = {}) {
    if (!parentElement || !msg) return;
    const senderType = msg.sender_type;
    const senderName = msg.sender_name || (senderType === 'ai' ? 'AI Assistant' : 'User');
    const currentUserId = window.currentUserInfo ? window.currentUserInfo.id : null;
    const isCurrentUser = msg.user_id === currentUserId;
    if (senderType === 'ai') {
        const aiTurnContainer = document.createElement('div');
        aiTurnContainer.className = 'ai-turn-container';
        if (msg.turn_id) aiTurnContainer.dataset.turnId = msg.turn_id;
        const hasContent = msg.content && msg.content.trim().length > 0;
        const hasFiles = msg.files && msg.files.length > 0;
        if (hasContent) {
            const messageBubble = document.createElement('div');
            messageBubble.className = 'message ai-message';
            const prompterId = msg.prompting_user_id;
            const prompterInfo = prompterId ? window.participantInfo?.[prompterId] : null;
            const prompterColor = prompterInfo?.color || '#dbeafe';
            const aiColor = window.participantInfo?.['AI']?.color || '#E0F2FE';
            messageBubble.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;
            const senderElem = document.createElement('p');
            senderElem.className = 'font-semibold text-sm mb-1 text-gray-800 italic';
            senderElem.textContent = prompterInfo ? `AI Assistant - Prompted by ${prompterInfo.name}` : 'AI Assistant';
            messageBubble.appendChild(senderElem);
            const contentElem = document.createElement('div');
            contentElem.className = 'text-gray-800 text-sm message-content';
            renderMarkdownAndKatex(msg.content, contentElem);
            messageBubble.appendChild(contentElem);
            aiTurnContainer.appendChild(messageBubble);
        }
        if (hasFiles) {
            const codeBlocksArea = document.createElement('div');
            codeBlocksArea.className = 'code-blocks-area';

            // --- FIX START: Determine the correct project_id from the runnable file's index ---
            const runFileIndex = msg.files.findIndex(f => f.path.endsWith('run.sh'));
            const runnableBlockIndex = runFileIndex !== -1 ? runFileIndex + 1 : msg.files.length;

            projectDataCache[msg.turn_id] = {
                name: `Project from turn ${msg.turn_id}`,
                files: msg.files,
                project_id: `code-block-turn${msg.turn_id}-${runnableBlockIndex}`,
                prompting_user_id: msg.prompting_user_id
            };
            // --- FIX END ---

            msg.files.forEach((file, index) => {
                const codeBlockIndex = index + 1;
                const isRunnable = file.path.endsWith('run.sh');
                const blockId = `code-block-turn${msg.turn_id}-${codeBlockIndex}`;
                const finalCodeContent = editedCodeBlocks[blockId] || file.content;
                
                // createCodeBlock now correctly assigns a unique ID to each block.
                // We no longer need to manually override the run.sh block's ID.
                const block = createCodeBlock(
                    file.language,
                    finalCodeContent,
                    file.content,
                    msg.turn_id,
                    codeBlockIndex,
                    codeBlocksArea,
                    isRunnable,
                    msg.prompting_user_id
                );
                
                block.querySelector('.block-title .title-text').textContent = file.path;
                initializeCodeBlockHistory(blockId, finalCodeContent);
            });
            aiTurnContainer.appendChild(codeBlocksArea);
        }
        if (aiTurnContainer.hasChildNodes()) {
             parentElement.appendChild(aiTurnContainer);
        }
    } else if (senderType === 'user' || senderType === 'system') {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col');
        messageDiv.setAttribute('data-sender', senderType);
        if (msg.id) messageDiv.setAttribute('data-message-id', String(msg.id));
        if (senderType === 'user') {
            if (isCurrentUser) {
                messageDiv.classList.add('self-end', 'ml-auto');
            } else {
                messageDiv.classList.add('self-start', 'mr-auto');
            }
            let bubbleColor = msg.sender_color;
            if (!bubbleColor && window.participantInfo && msg.user_id) {
                const participant = window.participantInfo[msg.user_id];
                if (participant) bubbleColor = participant.color;
            }
            if (bubbleColor) {
                messageDiv.style.backgroundColor = bubbleColor;
            } else {
                messageDiv.classList.add('bg-gray-200');
            }
        } else {
             messageDiv.classList.add('bg-slate-200', 'self-center', 'mx-auto', 'text-xs', 'italic');
        }
        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-gray-800');
        senderElem.textContent = senderName;
        messageDiv.appendChild(senderElem);
        const contentElem = document.createElement('div');
        contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');
        const cleanContent = (msg.content || '').replace(/@\w+/g, '').trim();
        contentElem.innerHTML = marked.parse(cleanContent);
        messageDiv.appendChild(contentElem);
        parentElement.appendChild(messageDiv);
    }
}

function stickHeader(header, scrollerRect) {
    if (!header || header.classList.contains('header-is-sticky-js')) return;
    
    const container = header.parentElement;
    const containerRect = container.getBoundingClientRect();
    const stickyId = `sticky-${header.classList[0]}-${Date.now()}`;
    
    if (document.getElementById(stickyId)) return;
    
    const stickyClone = header.cloneNode(true);
    stickyClone.id = stickyId;
    stickyClone.style.position = 'fixed';
    stickyClone.style.top = `${scrollerRect.top}px`;
    stickyClone.style.left = `${containerRect.left}px`;
    stickyClone.style.width = `${containerRect.width}px`;
    stickyClone.style.zIndex = '1000';
    stickyClone.style.backgroundColor = '#e5e7eb';
    stickyClone.style.borderRadius = '0';
    stickyClone.style.boxShadow = 'none';
    stickyClone.style.overflow = 'hidden';
    
    const originalButtons = header.querySelectorAll('button');
    const cloneButtons = stickyClone.querySelectorAll('button');
    
    cloneButtons.forEach((cloneBtn, index) => {
        const originalBtn = originalButtons[index];
        if (originalBtn) {
            cloneBtn.disabled = originalBtn.disabled;
            cloneBtn.className = originalBtn.className;
            cloneBtn.innerHTML = originalBtn.innerHTML;
            cloneBtn.dataset.status = originalBtn.dataset.status;
            
            cloneBtn.onclick = function(e) {
                e.preventDefault();
                e.stopPropagation();
                originalBtn.click();
            };
            
            const syncButton = () => {
                cloneBtn.disabled = originalBtn.disabled;
                cloneBtn.className = originalBtn.className;
                cloneBtn.innerHTML = originalBtn.innerHTML;
                cloneBtn.dataset.status = originalBtn.dataset.status;
            };
            
            const observer = new MutationObserver(syncButton);
            observer.observe(originalBtn, { 
                attributes: true, 
                attributeFilter: ['class', 'disabled', 'data-status'],
                childList: true,
                subtree: true
            });
            
            cloneBtn._syncObserver = observer;
        }
    });
    
    document.body.appendChild(stickyClone);
    header.classList.add('header-is-sticky-js');
    header.dataset.stickyId = stickyId;
}

function handleAiThinking(payload) {
    const { turn_id, prompting_user_id, prompting_user_name } = payload;
    let turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (turnContainer) return; // Already exists

    turnContainer = document.createElement('div');
    turnContainer.className = 'ai-turn-container';
    turnContainer.dataset.turnId = turn_id;

    const messageBubble = document.createElement('div');
    messageBubble.className = 'message ai-message';

    // Look up prompter info safely, with a fallback to the name from the payload
    const prompterInfo = window.participantInfo ? window.participantInfo[prompting_user_id] : null;
    const finalPrompterName = prompterInfo ? prompterInfo.name : prompting_user_name;
    const prompterColor = prompterInfo ? prompterInfo.color : '#dbeafe';
    const aiColor = window.participantInfo && window.participantInfo['AI'] ? window.participantInfo['AI'].color : '#E0F2FE';

    messageBubble.style.background = `linear-gradient(to right, ${aiColor}, ${prompterColor})`;

    const senderElem = document.createElement('p');
    senderElem.className = 'font-semibold text-sm mb-1 text-gray-800 italic';
    senderElem.textContent = `AI Assistant - Prompted by ${finalPrompterName}`;

    const contentArea = document.createElement('div');
    contentArea.className = 'text-gray-800 text-sm message-content live-ai-content-area';
    contentArea.innerHTML = '<span class="loading-dots"></span>';

    messageBubble.appendChild(senderElem);
    messageBubble.appendChild(contentArea);
    turnContainer.appendChild(messageBubble);
    chatHistory.appendChild(turnContainer);

    currentStreamingAnswerElement = contentArea;
    scrollToBottom('smooth');
}

function unstickHeader(header) {
    if (!header || !header.classList.contains('header-is-sticky-js')) return;

    const stickyId = header.dataset.stickyId;
    if (stickyId) {
        const stickyClone = document.getElementById(stickyId);
        if (stickyClone) {
            // Clean up button observers
            const cloneButtons = stickyClone.querySelectorAll('button');
            cloneButtons.forEach(btn => {
                if (btn._syncObserver) {
                    btn._syncObserver.disconnect();
                    delete btn._syncObserver;
                }
            });
            
            // NEW: Clean up status observer
            const cloneStatusSpan = stickyClone.querySelector('.block-status');
            if (cloneStatusSpan && cloneStatusSpan._syncObserver) {
                cloneStatusSpan._syncObserver.disconnect();
                delete cloneStatusSpan._syncObserver;
            }
            
            stickyClone.remove();
        }
    }

    header.classList.remove('header-is-sticky-js');
    delete header.dataset.stickyId;
}

function connectWebSocket() {
    let sessionId = getSessionIdFromPath();
    if (!sessionId) {
        addErrorMessage("Cannot connect to chat: Invalid session ID in URL.");
        setInputDisabledState(true, false);
        return;
    }
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${sessionId}/${clientId}`;
    try {
        const ws = new WebSocket(wsUrl);
        websocket = ws;
        ws.onopen = () => { console.log("[WS_CLIENT] WebSocket connection opened."); setInputDisabledState(false, false); addSystemMessage("Connected to the chat server."); };
        ws.onmessage = (event) => {
            if (typeof event.data !== 'string') return;
            const messageData = JSON.parse(event.data);
            
            // --- LOGGING ---
            console.log("[WebSocket Client] Received message:", messageData);
            // --- END LOGGING ---

            if (event.data.startsWith("<ERROR>")) { addErrorMessage(event.data.substring(7)); finalizeTurnOnErrorOrClose(); return; }
            
            switch (messageData.type) {
                case 'ai_thinking': handleAiThinking(messageData.payload); break;
                case 'project_header': handleProjectHeader(messageData.payload); break;
                case 'ai_chunk': handleAnswerChunk(messageData.payload); break;
                case 'end_answer_stream': handleEndAnswerStream(); break;
                case 'start_file_stream': handleStartFileStream(messageData.payload); break;
                case 'file_chunk': handleFileChunk(messageData.payload); break;
                case 'end_file_stream': handleEndFileStream(messageData.payload); break;
                case 'ai_stream_end': finalizeTurnOnErrorOrClose(); break;
                case 'new_message': if (messageData.payload && messageData.payload.client_id_temp !== clientId) { renderSingleMessage(messageData.payload, chatHistory, true, {}); } scrollToBottom('smooth'); break;
                case 'participant_typing': handleTypingIndicators(messageData.payload); break;
                case 'participants_update': updateAndDisplayParticipants(messageData.payload); break;
                case 'session_deleted': const chatTitle = document.getElementById('chat-session-title'); if (chatTitle) chatTitle.textContent = `[Deleted] ${chatTitle.textContent}`; const banner = document.createElement('div'); banner.className = 'text-center text-sm text-red-700 bg-red-100 p-2 rounded-md font-semibold'; banner.textContent = 'The host has deleted this session. The chat is now read-only.'; chatHistory.prepend(banner); setInputDisabledState(true, false); break;
                default: handleStructuredMessage(messageData); break;
            }
        };
        ws.onclose = (event) => { console.error(`[WS_CLIENT] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`); };
        ws.onerror = (event) => { console.error("[WS_CLIENT] WebSocket error:", event); };
    } catch (error) {
        console.error("WebSocket creation error:", error);
    }
}



function handleAnswerChunk(payload) {
    if (!currentStreamingAnswerElement) {
        console.warn("handleAnswerChunk called without a streaming element.");
        return; 
    }

    const loadingDots = currentStreamingAnswerElement.querySelector('.loading-dots');
    if (loadingDots) {
        currentStreamingAnswerElement.innerHTML = ''; // Clear the dots
    }
    
    // This is the fix for the [object Object] bug.
    // We now directly use the payload, which the server has corrected to be a simple string.
    const currentContent = (currentStreamingAnswerElement.dataset.rawContent || '') + payload;
    currentStreamingAnswerElement.dataset.rawContent = currentContent;
    renderMarkdownAndKatex(currentContent, currentStreamingAnswerElement);
    
    scrollToBottom('auto');
}

function handleProjectHeader(payload) {
    const { turn_id, name, prompting_user_id, prompting_user_name } = payload;
    const turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (!turnContainer) {
        console.error("Project header received but no thinking container exists for turn " + turn_id);
        return;
    }

    // Initialize the project data cache for this turn
    if (!projectDataCache[turn_id]) {
        projectDataCache[turn_id] = { 
            name: name,
            files: [],
            project_id: null, // Will be set when we find run.sh
            prompting_user_id: prompting_user_id
        };
    }

    const messageBubble = turnContainer.querySelector('.ai-message');
    const contentArea = turnContainer.querySelector('.live-ai-content-area');

    if (messageBubble && contentArea) {
        // The sender name should NOT be changed. It's already correct.
        // const senderElem = turnContainer.querySelector('.ai-message .font-semibold');

        // Create a new, separate header element for the project title
        const headerElement = document.createElement('div');
        headerElement.className = 'text-lg font-semibold text-gray-800 mt-2 mb-1 pb-1 border-b';
        headerElement.textContent = name;
        
        // Insert the header before the content area (where the dots are)
        messageBubble.insertBefore(headerElement, contentArea);

        // Clear the dots and prepare for the streaming description
        contentArea.innerHTML = '';
        currentStreamingAnswerElement = contentArea;
    }
}

function handleStartFileStream(payload) {
    const { turn_id, prompting_user_id, path, language } = payload;
    let turnContainer = document.querySelector(`.ai-turn-container[data-turn-id='${turn_id}']`);
    if (!turnContainer) {
        turnContainer = document.createElement('div');
        turnContainer.className = 'ai-turn-container';
        turnContainer.dataset.turnId = turn_id;
        chatHistory.appendChild(turnContainer);
    }
    const codeBlocksArea = turnContainer.querySelector('.code-blocks-area') || document.createElement('div');
    if (!codeBlocksArea.parentElement) {
        codeBlocksArea.className = 'code-blocks-area';
        turnContainer.appendChild(codeBlocksArea);
    }
    streamingCodeBlockCounter++;
    const isRunnable = path.endsWith('run.sh');
    const blockId = `code-block-turn${turn_id}-${streamingCodeBlockCounter}`;

    if (isRunnable && projectDataCache[turn_id]) {
        projectDataCache[turn_id].project_id = blockId;
    }
    
    const newBlock = createCodeBlock(
        language || 'plaintext', '', '', turn_id, 
        streamingCodeBlockCounter, codeBlocksArea, isRunnable,
        prompting_user_id
    );
    newBlock.querySelector('.block-title .title-text').textContent = path;
    
    const newFileData = { path: path, content: "", language: language };
    if (projectDataCache[turn_id]) {
        projectDataCache[turn_id].files.push(newFileData);
    }

    currentStreamingFile = { 
        container: newBlock, 
        codeElement: newBlock.querySelector('code'), 
        path: path, 
        fileData: newFileData,
        highlightTimeout: null // For debouncing the highlighter
    };
}

function handleFileChunk(payload) {
    if (!currentStreamingFile || !currentStreamingFile.codeElement) return;
    
    const codeElement = currentStreamingFile.codeElement;
    codeElement.textContent += payload.content;

    if (currentStreamingFile.fileData) {
        currentStreamingFile.fileData.content += payload.content;
    }

    // Debounce the syntax highlighting for better performance
    clearTimeout(currentStreamingFile.highlightTimeout);
    currentStreamingFile.highlightTimeout = setTimeout(() => {
        const cursorPos = getCursorPosition(codeElement);
        Prism.highlightElement(codeElement);
        if (document.activeElement === codeElement) {
            setCursorPosition(codeElement, cursorPos);
        }
    }, 50); // Highlight after a 50ms pause in streaming
}


document.addEventListener('DOMContentLoaded', async () => {
    await initializeCurrentUser();
    setInputDisabledState(true, false);

    if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
        marked.setOptions({
            gfm: true, breaks: true, sanitize: false, smartLists: true, smartypants: false,
        });
    }

    if (chatForm && messageInput && sendButton && stopAiButton) {
        let typingTimeout;
        const sendTypingSignal = (isTyping) => {
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({ type: 'user_typing', payload: { is_typing: isTyping } }));
            }
        };

        messageInput.addEventListener('input', () => {
            if (messageInput.value.trim() === '' && typingTimeout) {
                clearTimeout(typingTimeout);
                typingTimeout = null;
                sendTypingSignal(false);
                return;
            }
            if (!typingTimeout) {
                sendTypingSignal(true);
            }
            clearTimeout(typingTimeout);
            typingTimeout = setTimeout(() => {
                sendTypingSignal(false);
                typingTimeout = null;
            }, 2000);
        });

        chatForm.addEventListener('submit', (event) => {
        event.preventDefault();
        
        // --- ADDED LOGGING ---
        console.log("Submit event fired.");

        if(typingTimeout) {
            clearTimeout(typingTimeout);
            typingTimeout = null;
            sendTypingSignal(false);
        }
        
        const userMessage = messageInput.value.trim();
        // --- ADDED LOGGING ---
        console.log("User message:", userMessage);

        if (!userMessage) {
            console.log("Message is empty. Aborting send.");
            return;
        }

        // --- ADDED LOGGING ---
        // Check WebSocket state. 1 means OPEN.
        console.log("WebSocket object:", websocket);
        console.log("WebSocket readyState:", websocket ? websocket.readyState : "Not defined");

        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            console.error("[FORM] WebSocket not open, cannot send message.");
            addErrorMessage("Cannot send message: Not connected to the server. Please refresh the page."); // Show error in UI
            return;
        }
        
        try {
            addUserMessage(userMessage);
            messageInput.value = '';

            const mentionRegex = /@(\w+)/gi;
            const recipients = (userMessage.match(mentionRegex) || []).map(m => m.substring(1).toUpperCase());
            
            currentTurnId++;
            
            if (recipients.includes("AI")) {
                if (!window.isAiConfigured) {
                    alert("AI provider has not been configured. Please go to User Settings to select a provider and add your API key.");
                    currentTurnId--;
                    return;
                }
                setInputDisabledState(true, true);
            }
            
            const messagePayload = {
                type: "chat_message",
                payload: {
                    user_input: userMessage,
                    turn_id: currentTurnId,
                    recipient_ids: recipients,
                    reply_to_id: null
                }
            };

            // --- ADDED LOGGING ---
            console.log("Sending payload:", JSON.stringify(messagePayload));
            websocket.send(JSON.stringify(messagePayload));
            console.log("Payload sent.");

        } catch (sendError) {
            // --- ADDED LOGGING ---
            console.error("Error during message send:", sendError);
            addErrorMessage(`Failed to send message: ${sendError.message}`);
        }
    });

        stopAiButton.addEventListener('click', () => {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
            websocket.send(JSON.stringify({
                type: "stop_ai_stream",
                payload: { client_id: clientId, session_id: getSessionIdFromPath(), turn_id: currentTurnId }
            }));
        });
    }

    const currentSessionId = getSessionIdFromPath();
    if (currentSessionId) {
        await loadAndDisplayChatHistory(currentSessionId);
        connectWebSocket();
    } else {
        if (messageInput) {
            setInputDisabledState(true, false);
        }
    }

    const chatHistoryScroller = document.getElementById('chat-history');
    if (chatHistoryScroller) {
        let rafId = null;
        const handleScroll = function() {
            if (rafId) cancelAnimationFrame(rafId);
            rafId = requestAnimationFrame(function() {
                const scrollerRect = chatHistoryScroller.getBoundingClientRect();
                const containers = chatHistoryScroller.querySelectorAll('.block-container');

                const measurements = [];
                containers.forEach(function(container) {
                    const header = container.querySelector('.block-header, .block-header');
                    const content = container.querySelector('pre, .block-output-console');
                    
                    if (header && content) {
                        measurements.push({
                            header: header,
                            headerRect: header.getBoundingClientRect(),
                            contentRect: content.getBoundingClientRect(),
                            container: container
                        });
                    }
                });

                measurements.forEach(function(m) {
                    const shouldStick = m.headerRect.top < scrollerRect.top && m.contentRect.bottom > scrollerRect.top;
                    
                    if (shouldStick) {
                        stickHeader(m.header, scrollerRect);
                        
                        const stickyId = m.header.dataset.stickyId;
                        if (stickyId) {
                            const stickyClone = document.getElementById(stickyId);
                            if (stickyClone) {
                                const headerHeight = stickyClone.offsetHeight;
                                const contentBottom = m.contentRect.bottom;
                                const viewportTop = scrollerRect.top + 1;
                                const availableSpace = contentBottom - viewportTop + 3;
                                
                                if (availableSpace < headerHeight && availableSpace > 0) {
                                    const clipAmount = headerHeight - availableSpace - 1;
                                    stickyClone.style.top = `${viewportTop - clipAmount - 1}px`;
                                    stickyClone.style.clipPath = `inset(${clipAmount}px 0 0 0)`;
                                    stickyClone.style.height = `${headerHeight}px`;
                                    stickyClone.style.borderBottomLeftRadius = '0.375rem';
                                    stickyClone.style.borderBottomRightRadius = '0.375rem';
                                } else if (availableSpace <= 2) {
                                    stickyClone.style.display = 'none';
                                } else {
                                    stickyClone.style.height = 'auto';
                                    stickyClone.style.top = `${scrollerRect.top}px`;
                                    stickyClone.style.display = 'flex';
                                    stickyClone.style.clipPath = 'none';
                                    stickyClone.style.borderBottomLeftRadius = '0';
                                    stickyClone.style.borderBottomRightRadius = '0';
                                }
                            }
                        }
                    } else {
                        unstickHeader(m.header);
                    }
                });
            });
        };

        chatHistoryScroller.addEventListener('scroll', function(e) {
            if (e.target === chatHistoryScroller) {
                handleScroll();
            }
        }, { passive: true });
    }
});