// --- JS Imports ---
import { marked } from 'marked';
import katex from 'katex';
import Prism from 'prismjs';

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

// --- Core Variables ---
let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0;

// --- Constants ---
const NO_THINK_PREFIX = "\\no_think";
const THINK_PREFIX = "\\think";

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
let supportedLanguagesConfig = {};
let currentAiTurnContainer = null;
let currentAnswerElement = null;
let currentThinkingArea = null;
let currentThinkingPreElement = null;
let currentCodeBlocksArea = null;
let codeBlockCounterThisTurn = 0;
let thinkingRequestedForCurrentTurn = false;
let accumulatedAnswerText = '';
let hasThinkingContentArrivedThisTurn = false;
let firstAnswerTokenReceived = false;
let activeStreamingCodeBlocks = new Map(); // blockId -> { element, language, content }
let streamingCodeBlockCounter = 0;




function addTerminalPrompt(outputPreElement, promptText) {
    if (!outputPreElement) return;
    
    const outputContainer = outputPreElement.closest('.block-container');
    if (!outputContainer) return;
    
    const inputField = document.createElement('input');
    inputField.type = 'text';
    inputField.style.border = 'none';
    inputField.style.outline = 'none';
    inputField.style.background = 'transparent';
    inputField.style.color = 'inherit';
    inputField.style.font = 'inherit';
    inputField.style.padding = '0';
    inputField.style.margin = '0';
    inputField.style.display = 'inline';
    inputField.style.width = 'auto';
    inputField.style.minWidth = '200px';
    
    outputPreElement.appendChild(inputField);
    inputField.focus();
    
    inputField.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const userInput = inputField.value;
            
            const textNode = document.createTextNode(userInput + '\n');
            outputPreElement.replaceChild(textNode, inputField);
            
            // FIX: Get the output block's ID and strip the prefix to find the original code block ID.
            const outputBlockId = outputContainer.id; // e.g., "output-for-code-block-turn1-1"
            const originalCodeBlockId = outputBlockId.replace('output-for-', ''); // results in "code-block-turn1-1"

            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({
                    type: 'code_input',
                    payload: { 
                        code_block_id: originalCodeBlockId, // Send the CORRECT ID
                        input: userInput + '\n' 
                    }
                }));
            }
        }
    });
}

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

// --- Configuration Loading ---
async function loadLanguagesConfig() {
    try {
        const response = await fetch('/static/languages.json');
        if (!response.ok) {
            console.error('Failed to fetch languages.json');
            return;
        }
        supportedLanguagesConfig = await response.json();
        console.log("Successfully loaded language config:", supportedLanguagesConfig);
    } catch (error) {
        console.error('Error loading languages.json:', error);
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

// --- Session Management ---
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

// --- UI Helper Functions ---
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

// --- Message Display Functions ---
function addUserMessage(text) {
    if (!chatHistory) {
        console.error("addUserMessage: chatHistory element not found.");
        return;
    }

    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'user-message', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-emerald-100', 'self-end', 'ml-auto');
    messageElement.setAttribute('data-sender', 'user');

    const senderElem = document.createElement('p');
    senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-emerald-700');
    let userName = 'User';

    if (typeof window.currentUserInfo === 'object' && window.currentUserInfo !== null && window.currentUserInfo.name) {
        userName = window.currentUserInfo.name;
    }
    senderElem.textContent = escapeHTML(userName);
    messageElement.appendChild(senderElem);

    const contentElem = document.createElement('div');
    contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');
    
    let displayedText = text;
    if (displayedText.startsWith(NO_THINK_PREFIX)) {
        displayedText = displayedText.substring(NO_THINK_PREFIX.length);
    } else if (displayedText.startsWith(THINK_PREFIX)) {
        displayedText = displayedText.substring(THINK_PREFIX.length);
    }

    contentElem.textContent = displayedText;
    messageElement.appendChild(contentElem);

    const timestampElem = document.createElement('p');
    timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-right');
    timestampElem.textContent = new Date().toLocaleString();
    messageElement.appendChild(timestampElem);

    chatHistory.appendChild(messageElement);
    setTimeout(() => scrollToBottom('smooth'), 50);
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

function setupNewAiTurn() {
    currentTurnId++;
    codeBlockCounterThisTurn = 0;
    accumulatedAnswerText = '';
    hasThinkingContentArrivedThisTurn = false;
    firstAnswerTokenReceived = false;

    // This map is for tracking blocks within a single turn and MUST be cleared.
    activeStreamingCodeBlocks.clear();
    // The main counter variable is NOT reset here, allowing it to be session-wide.

    currentAiTurnContainer = null;
    currentThinkingArea = null;
    currentThinkingPreElement = null;
    currentAnswerElement = null;
    currentCodeBlocksArea = null;

    console.log(`[setupNewAiTurn] Starting setup for Turn ID: ${currentTurnId}. thinkingRequestedForCurrentTurn is: ${thinkingRequestedForCurrentTurn}`);

    currentAiTurnContainer = document.createElement('div');
    currentAiTurnContainer.classList.add('ai-turn-container');
    currentAiTurnContainer.dataset.turnId = currentTurnId;

    // Thinking Area Setup
    currentThinkingArea = document.createElement('div');
    currentThinkingArea.classList.add('thinking-area');
    currentThinkingArea.dataset.turnId = currentTurnId;
    if (!thinkingRequestedForCurrentTurn) {
        currentThinkingArea.style.display = 'none';
    }
    
    const details = document.createElement('details');
    details.id = `thinking-details-${currentTurnId}`;
    const summary = document.createElement('summary');
    summary.classList.add('thinking-summary');
    const summaryTextSpan = document.createElement('span');
    summaryTextSpan.classList.add('text');
    summaryTextSpan.textContent = 'Show Thinking';
    const summaryDotsSpan = document.createElement('span');
    summaryDotsSpan.classList.add('dots');
    summary.appendChild(summaryTextSpan);
    summary.appendChild(summaryDotsSpan);
    currentThinkingPreElement = document.createElement('pre');
    details.appendChild(summary);
    details.appendChild(currentThinkingPreElement);
    currentThinkingArea.appendChild(details);
    currentAiTurnContainer.appendChild(currentThinkingArea);
    
    details.addEventListener('toggle', (event) => {
        const textSpan = event.target.querySelector('.thinking-summary .text');
        if (!textSpan) return;
        textSpan.textContent = event.target.open ? 'Hide Thinking' : 'Show Thinking';
    });

    // Answer Element (AI Message Bubble) Setup
    currentAnswerElement = document.createElement('div');
    currentAnswerElement.classList.add('message', 'ai-message', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-sky-100', 'self-start', 'mr-auto');

    const senderElem = document.createElement('p');
    senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-sky-700');
    senderElem.textContent = 'AI';
    currentAnswerElement.appendChild(senderElem);

    const liveContentDiv = document.createElement('div');
    liveContentDiv.classList.add('text-gray-800', 'text-sm', 'message-content', 'live-ai-content-area');
    currentAnswerElement.appendChild(liveContentDiv);

    if (thinkingRequestedForCurrentTurn) {
        currentAnswerElement.style.display = 'none';
    } else {
        const loadingSpan = document.createElement('span');
        loadingSpan.classList.add('loading-dots');
        liveContentDiv.appendChild(loadingSpan);
    }
    currentAiTurnContainer.appendChild(currentAnswerElement);

    // Code Blocks Area Setup
    currentCodeBlocksArea = document.createElement('div');
    currentCodeBlocksArea.classList.add('code-blocks-area');
    currentAiTurnContainer.appendChild(currentCodeBlocksArea);

    chatHistory.appendChild(currentAiTurnContainer);
    console.log(`[setupNewAiTurn] Finished setup for Turn ID: ${currentTurnId}.`);
}

// --- Live Rendering ---
function renderLiveMessage(fullContent) {
    if (!currentAiTurnContainer) return;

    if (!firstAnswerTokenReceived && fullContent.trim().length > 0) {
        if (currentAnswerElement && currentAnswerElement.style.display === 'none') {
            currentAnswerElement.style.display = '';
        }
        const loadingDots = currentAnswerElement ? currentAnswerElement.querySelector('.loading-dots') : null;
        if (loadingDots) loadingDots.remove();
        firstAnswerTokenReceived = true;
    }

    const contentArea = currentAnswerElement.querySelector('.live-ai-content-area');
    const codeArea = currentCodeBlocksArea;

    if (!contentArea || !codeArea) return;
    
    parseAndRenderStreamingContent(fullContent, contentArea, codeArea, currentTurnId);
    scrollToBottom('auto');
}

function parseAndRenderAiContent(contentString, answerBubbleContentElement, codeBlocksDivElement, turnIdSuffix, editedCodeBlocks = {}) {
    if (!contentString || !answerBubbleContentElement || !codeBlocksDivElement) return;

    const KATEX_PLACEHOLDER_PREFIX_HISTORICAL = `%%HISTORICAL_KATEX_PLACEHOLDER_${turnIdSuffix}_`;

    let contentForProcessing = contentString;
    let historicalCodeBlockCounter = 0;
    const codeBlockRegex = /```(\w*)\n([\s\S]*?)\n```/g;
    const extractedCodeBlocks = [];
    let tempContentForCodeExtraction = contentForProcessing;
    let contentAfterCodeExtraction = "";
    let lastCodeMatchEndIndex = 0;
    let matchCode;

    codeBlocksDivElement.innerHTML = '';

    while ((matchCode = codeBlockRegex.exec(tempContentForCodeExtraction)) !== null) {
        historicalCodeBlockCounter++;
        const language = matchCode[1] || 'plaintext';
        const originalCode = matchCode[2];
        const placeholder = `%%HISTORICAL_CODE_BLOCK_${historicalCodeBlockCounter}%%`;

        const blockId = `code-block-turn${turnIdSuffix}-${historicalCodeBlockCounter}`;

        extractedCodeBlocks.push({ 
            language, 
            originalCode, 
            placeholder, 
            index: historicalCodeBlockCounter,
            id: blockId 
        });

        contentAfterCodeExtraction += tempContentForCodeExtraction.substring(lastCodeMatchEndIndex, matchCode.index);
        contentAfterCodeExtraction += placeholder;
        lastCodeMatchEndIndex = codeBlockRegex.lastIndex;
    }
    contentAfterCodeExtraction += tempContentForCodeExtraction.substring(lastCodeMatchEndIndex);
    contentForProcessing = contentAfterCodeExtraction;

    extractedCodeBlocks.forEach(block => {
        const finalCodeContent = editedCodeBlocks[block.id] || block.originalCode;

        // CORRECTED CALL: Pass BOTH the final content for display AND the true original content for the dataset
        createCodeBlock(block.language, finalCodeContent, block.originalCode, turnIdSuffix, block.index, codeBlocksDivElement, false);

        initializeCodeBlockHistory(block.id, finalCodeContent);
        const replacementHTML = `<a href="#${block.id}" class="code-block-link text-blue-600 hover:underline">[Code Block ${block.index}]</a>`;

        contentForProcessing = contentForProcessing.replace(block.placeholder, replacementHTML);
    });

    const storedKatex = {};
    let katexPlaceholderIndex = 0;
    const katexRegexGlobal = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|(?<!\\)\$((?:\\\$|[^$])+?)(?<!\\)\$/g;
    let textForMarkdownParsing = contentForProcessing.replace(katexRegexGlobal, (match, displayContent, inlineContent) => {
        const isDisplayMode = !!displayContent;
        const katexString = displayContent || inlineContent;
        const cleanedKatexString = katexString.replace(/\\([$])/g, '$1');
        let katexHtml = '';
        try {
            katexHtml = katex.renderToString(cleanedKatexString, {
                displayMode: isDisplayMode, throwOnError: false, output: "html", strict: false
            });
        } catch (e) {
            katexHtml = `<span class="katex-error" title="${escapeHTML(e.toString())}">${escapeHTML(match)}</span>`;
        }
        const placeholderId = `${KATEX_PLACEHOLDER_PREFIX_HISTORICAL}${katexPlaceholderIndex++}`;
        storedKatex[placeholderId] = katexHtml;
        return placeholderId;
    });

    answerBubbleContentElement.innerHTML = marked.parse(textForMarkdownParsing);

    if (Object.keys(storedKatex).length > 0) {
        const walker = document.createTreeWalker(answerBubbleContentElement, NodeFilter.SHOW_TEXT, null, false);
        let node;
        const textNodesToModify = [];
        while (node = walker.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes(KATEX_PLACEHOLDER_PREFIX_HISTORICAL)) {
                textNodesToModify.push(node);
            }
        }
        textNodesToModify.forEach(textNode => {
            let currentTextValue = textNode.nodeValue;
            const parent = textNode.parentNode;
            if (!parent) return;
            const fragment = document.createDocumentFragment();
            let lastSplitEnd = 0;
            const placeholderScanRegex = new RegExp(`(${KATEX_PLACEHOLDER_PREFIX_HISTORICAL}\\d+)`, 'g');
            let placeholderMatch;
            while((placeholderMatch = placeholderScanRegex.exec(currentTextValue)) !== null) {
                const placeholderId = placeholderMatch[1];
                const matchStartIndex = placeholderMatch.index;
                if (matchStartIndex > lastSplitEnd) {
                    fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd, matchStartIndex)));
                }
                if (storedKatex[placeholderId]) {
                    const katexWrapperSpan = document.createElement('span');
                    katexWrapperSpan.innerHTML = storedKatex[placeholderId];
                    fragment.appendChild(katexWrapperSpan.firstChild || katexWrapperSpan);
                } else {
                    fragment.appendChild(document.createTextNode(placeholderId));
                }
                lastSplitEnd = placeholderScanRegex.lastIndex;
            }
            if (lastSplitEnd < currentTextValue.length) {
                fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd)));
            }
            parent.replaceChild(fragment, textNode);
        });
    }
}

function parseAndRenderStreamingContent(contentString, answerBubbleContentElement, codeBlocksDivElement, turnIdSuffix) {
    if (!contentString || !answerBubbleContentElement || !codeBlocksDivElement) return;

    const codeBlockRegex = /```(\w*)\n?([\s\S]*?)(?:\n```|$)/g;
    let match;
    let lastIndex = 0;
    let contentWithPlaceholders = '';

    while ((match = codeBlockRegex.exec(contentString)) !== null) {
        const [fullMatch, language, code] = match;
        const isComplete = fullMatch.endsWith('\n```') || fullMatch.endsWith('```');
        const startIndex = match.index;

        contentWithPlaceholders += contentString.substring(lastIndex, startIndex);

        if (!activeStreamingCodeBlocks.has(startIndex)) {
            streamingCodeBlockCounter++;
            // Create the new block, passing the code as both the display content AND the original dataset content
            const newBlockElement = createCodeBlock(
                language || 'plaintext', 
                code, 
                code, // The new originalCodeForDataset argument
                turnIdSuffix, 
                streamingCodeBlockCounter, 
                codeBlocksDivElement, 
                true
            );
            activeStreamingCodeBlocks.set(startIndex, {
                blockId: newBlockElement.id,
                language: language || 'plaintext',
                content: code,
                isComplete: isComplete
            });
        } else {
            const blockData = activeStreamingCodeBlocks.get(startIndex);
            const newLanguage = language || 'plaintext';
            blockData.language = newLanguage;
            blockData.content = code;
            blockData.isComplete = isComplete;
            updateStreamingCodeBlockContent(blockData.blockId, newLanguage, code, isComplete);
        }

        const blockData = activeStreamingCodeBlocks.get(startIndex);
        const blockNumber = blockData.blockId.split('-').pop();
        const blockId = `code-block-turn${turnIdSuffix}-${blockNumber}`;
        const linkHTML = `<a href="#${blockId}" class="code-block-link text-blue-600 hover:underline">[Code Block ${blockNumber}]</a>`;
        contentWithPlaceholders += linkHTML;
        lastIndex = codeBlockRegex.lastIndex;
    }

    contentWithPlaceholders += contentString.substring(lastIndex);

    const KATEX_PLACEHOLDER_PREFIX_STREAMING = `%%STREAMING_KATEX_PLACEHOLDER_${turnIdSuffix}_`;
    const storedKatex = {};
    let katexPlaceholderIndex = 0;

    const katexRegexGlobal = /(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$|(?<!\\)\$((?:\\\$|[^$])+?)(?<!\\)\$/g;
    let textForMarkdownParsing = contentWithPlaceholders.replace(katexRegexGlobal, (match, displayContent, inlineContent) => {
        const isDisplayMode = !!displayContent;
        const katexString = displayContent || inlineContent;
        const cleanedKatexString = katexString.replace(/\\([$])/g, '$1');
        let katexHtml = '';
        try {
            katexHtml = katex.renderToString(cleanedKatexString, {
                displayMode: isDisplayMode, throwOnError: false, output: "html", strict: false
            });
        } catch (e) {
            katexHtml = `<span class="katex-error" title="${escapeHTML(e.toString())}">${escapeHTML(match)}</span>`;
        }
        const placeholderId = `${KATEX_PLACEHOLDER_PREFIX_STREAMING}${katexPlaceholderIndex++}`;
        storedKatex[placeholderId] = katexHtml;
        return placeholderId;
    });

    answerBubbleContentElement.innerHTML = marked.parse(textForMarkdownParsing);

    if (Object.keys(storedKatex).length > 0) {
        const walker = document.createTreeWalker(answerBubbleContentElement, NodeFilter.SHOW_TEXT, null, false);
        let node;
        const textNodesToModify = [];
        while (node = walker.nextNode()) {
            if (node.nodeValue && node.nodeValue.includes(KATEX_PLACEHOLDER_PREFIX_STREAMING)) {
                textNodesToModify.push(node);
            }
        }
        textNodesToModify.forEach(textNode => {
            let currentTextValue = textNode.nodeValue;
            const parent = textNode.parentNode;
            if (!parent) return;
            const fragment = document.createDocumentFragment();
            let lastSplitEnd = 0;
            const placeholderScanRegex = new RegExp(`(${KATEX_PLACEHOLDER_PREFIX_STREAMING}\\d+)`, 'g');
            let placeholderMatch;
            while((placeholderMatch = placeholderScanRegex.exec(currentTextValue)) !== null) {
                const placeholderId = placeholderMatch[1];
                const matchStartIndex = placeholderMatch.index;
                if (matchStartIndex > lastSplitEnd) {
                    fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd, matchStartIndex)));
                }
                if (storedKatex[placeholderId]) {
                    const katexWrapperSpan = document.createElement('span');
                    katexWrapperSpan.innerHTML = storedKatex[placeholderId];
                    fragment.appendChild(katexWrapperSpan.firstChild || katexWrapperSpan);
                } else {
                    fragment.appendChild(document.createTextNode(placeholderId));
                }
                lastSplitEnd = placeholderScanRegex.lastIndex;
            }
            if (lastSplitEnd < currentTextValue.length) {
                fragment.appendChild(document.createTextNode(currentTextValue.substring(lastSplitEnd)));
            }
            parent.replaceChild(fragment, textNode);
        });
    }
}

function updateStreamingCodeBlockContent(blockId, language, content, isComplete) {
    const container = document.getElementById(blockId);
    if (!container) return;

    const codeElement = container.querySelector('code');
    const dotsSpan = container.querySelector('.streaming-dots');
    const titleTextSpan = container.querySelector('.block-title .title-text');
    const currentLanguage = container.dataset.language;

    const rawLang = (language || 'plaintext').trim().toLowerCase();
    const canonicalLang = LANGUAGE_ALIASES[rawLang] || 'plaintext';

    if (canonicalLang && canonicalLang !== currentLanguage) {
        container.dataset.language = canonicalLang;
        if (titleTextSpan) {
            const blockNumber = blockId.split('-').pop();
            titleTextSpan.textContent = `Code Block ${blockNumber} (${canonicalLang})`;
        }
        
        const prismLang = PRISM_LANGUAGE_MAP[canonicalLang] || canonicalLang;
        if (codeElement) {
            codeElement.className = `language-${prismLang}`;
        }

        const runStopBtn = container.querySelector('.run-code-btn');
        if (runStopBtn) {
            const isLanguageSupported = supportedLanguagesConfig[canonicalLang]?.executable;
            if (isLanguageSupported) {
                runStopBtn.style.display = '';
                runStopBtn.title = 'Run Code';
            } else {
                runStopBtn.style.display = 'none';
                runStopBtn.title = `Run Code (language '${canonicalLang}' not supported for execution)`;
            }
        }
        
        initializeCodeBlockHistory(blockId, content);
    }
    
    if (codeElement) {
        const cursorPos = getCursorPosition(codeElement);
        const wasEditing = document.activeElement === codeElement;
        
        codeElement.textContent = content;
        
        if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
            try {
                Prism.highlightElement(codeElement);
                if (wasEditing) {
                    setCursorPosition(codeElement, cursorPos);
                    codeElement.focus();
                }
            } catch (e) {
                console.error(`Prism highlight error:`, e);
            }
        }
        
        if (window.codeBlockHistories && window.codeBlockHistories.has(blockId)) {
            const historyData = window.codeBlockHistories.get(blockId);
            if (historyData.history.length === 1) {
                historyData.history[0] = content;
            }
        }
    }
    
    if (isComplete && dotsSpan) {
        dotsSpan.remove();
    }
}

function updateAllRunButtonStates() {
    document.querySelectorAll('.run-code-btn').forEach(btn => {
        const container = btn.closest('.block-container');
        const lang = container ? container.dataset.language : null;
        const isExecutable = lang ? supportedLanguagesConfig[lang]?.executable : false;

        if (isExecutable && !btn.disabled) {
            btn.disabled = false;
            btn.title = 'Run Code';
        }
    });
}

function createCodeBlock(language, codeContent, originalCodeForDataset, turnIdSuffix, codeBlockIndex, codeBlocksAreaElement, isStreaming = false) {
   if (!codeBlocksAreaElement) {
       console.error("createCodeBlock: Code blocks area element is null!");
       return;
   }

   const rawLang = (language || 'plaintext').trim().toLowerCase();
   const canonicalLang = LANGUAGE_ALIASES[rawLang] || 'plaintext';
   const isLanguageSupported = supportedLanguagesConfig[canonicalLang]?.executable;
   const prismLang = PRISM_LANGUAGE_MAP[canonicalLang] || canonicalLang;

   const blockId = `code-block-turn${turnIdSuffix}-${codeBlockIndex}`;

   const container = document.createElement('div');
   container.classList.add('block-container');
   container.id = blockId;
   container.dataset.language = canonicalLang;
   // Set the dataset to the TRUE original content, not the potentially edited content
   container.dataset.originalContent = originalCodeForDataset;

   const codeHeader = document.createElement('div');
   codeHeader.classList.add('block-header');

   const codeButtonsDiv = document.createElement('div');
   codeButtonsDiv.classList.add('block-buttons');

   const runStopBtn = document.createElement('button');
   runStopBtn.classList.add('run-code-btn', 'block-action-btn');
   runStopBtn.dataset.status = 'idle';
   runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
   runStopBtn.addEventListener('click', handleRunStopCodeClick);
   runStopBtn.disabled = false;
   runStopBtn.title = 'Run Code';

   if (!isLanguageSupported) {
       runStopBtn.style.display = 'none';
   }

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

   codeButtonsDiv.appendChild(runStopBtn);
   codeButtonsDiv.appendChild(restoreBtn);
   codeButtonsDiv.appendChild(toggleCodeBtn);
   codeButtonsDiv.appendChild(copyCodeBtn);

   const codeTitle = document.createElement('span');
   codeTitle.classList.add('block-title');
   const titleTextSpan = document.createElement('span');
   titleTextSpan.classList.add('title-text');
   titleTextSpan.textContent = `Code Block ${codeBlockIndex} (${canonicalLang})`;
   codeTitle.appendChild(titleTextSpan);

   if (isStreaming) {
       const dotsSpan = document.createElement('span');
       dotsSpan.classList.add('streaming-dots');
       dotsSpan.textContent = '...';
       codeTitle.appendChild(dotsSpan);
   }

   codeHeader.appendChild(codeButtonsDiv);
   codeHeader.appendChild(codeTitle);

   const preElement = document.createElement('pre');
   preElement.classList.add('manual');
   const codeElement = document.createElement('code');
   codeElement.className = `language-${prismLang}`;
   codeElement.setAttribute('contenteditable', 'true');
   codeElement.setAttribute('spellcheck', 'false');
   // Display the final content (which could be an edit)
   codeElement.textContent = codeContent;

   initializeCodeBlockHistory(blockId, codeContent);

   preElement.appendChild(codeElement);
   container.appendChild(codeHeader);
   container.appendChild(preElement);
   codeBlocksAreaElement.appendChild(container);

   toggleCodeBtn.addEventListener('click', () => {
       const isHidden = preElement.classList.toggle('hidden');
       toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide';
   });

   copyCodeBtn.addEventListener('click', async () => {
       try {
           await navigator.clipboard.writeText(codeElement.textContent || '');
           copyCodeBtn.textContent = 'Copied!';
           setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500);
       } catch (err) {
           console.error('Failed to copy code: ', err);
       }
   });

   restoreBtn.addEventListener('click', async () => {
        const originalContent = container.dataset.originalContent;
        const sessionId = getSessionIdFromPath();
        if (!sessionId || !window.csrfTokenRaw) return;

        try {
            const response = await fetch(`/api/sessions/${sessionId}/edited-blocks/${blockId}`, {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': window.csrfTokenRaw }
            });

            if (response.ok) {
                const cursorPos = getCursorPosition(codeElement);
                codeElement.textContent = originalContent;

                if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
                    try {
                        Prism.highlightElement(codeElement);
                        setCursorPosition(codeElement, Math.min(cursorPos, originalContent.length));
                    } catch (e) { console.error(`Prism highlight error:`, e); }
                }
                // Add the restored state to the undo history instead of wiping it
                saveCodeBlockState(blockId, originalContent);
            } else {
                const error = await response.json();
                alert(`Failed to restore code block: ${error.detail || 'Server error'}`);
            }
        } catch (error) {
            console.error("Error restoring code block:", error);
            alert("An error occurred while restoring the code block.");
        }
   });

   codeElement.addEventListener('keydown', (event) => {
       handleCodeBlockKeydown(event, blockId);
   });

   codeElement.addEventListener('blur', () => {
       const content = codeElement.textContent || '';
       saveCodeBlockContent(blockId, content);
   });

   codeElement.addEventListener('input', () => {
       const cursorPos = getCursorPosition(codeElement);
       const content = codeElement.textContent || '';

       setTimeout(() => {
           if (typeof Prism !== 'undefined' && typeof Prism.highlightElement === 'function') {
               try {
                   Prism.highlightElement(codeElement);
                   setCursorPosition(codeElement, cursorPos);
               } catch (e) {
                   console.error(`Prism highlight error:`, e);
               }
           }
       }, 10);

       saveCodeBlockState(blockId, content);
   });

   return container;
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

function createOutputHeaderHTML(blockNumber, statusText = 'Running...', statusClass = 'running') {
    return `
        <div class="block-buttons">
            <button class="run-code-btn block-action-btn" style="display: none;">Run</button>
            <button class="toggle-output-btn block-action-btn">Hide</button>
            <button class="copy-output-btn block-action-btn">Copy</button>
        </div>
        <span class="block-title">Output Block ${blockNumber}</span>
        <span class="block-status ${statusClass}">${statusText}</span>
    `;
}

async function handleRunStopCodeClick(event) {
    const button = event.currentTarget;
    const container = button.closest('.block-container');
    if (!container) return;

    const codeBlockId = container.id;
    const language = container.dataset.language;
    const codeElement = container.querySelector('code');
    const code = codeElement ? codeElement.textContent || '' : '';

    saveCodeBlockContent(codeBlockId, code);

    let outputContainer = document.getElementById(`output-for-${codeBlockId}`);
    if (!outputContainer) {
        outputContainer = document.createElement('div');
        outputContainer.id = `output-for-${codeBlockId}`;
        outputContainer.className = 'block-container';

        const blockNumber = codeBlockId.split('-').pop();

        const outputHeader = document.createElement('div');
        outputHeader.className = 'block-header';
        outputHeader.innerHTML = createOutputHeaderHTML(blockNumber);
        const outputConsoleDiv = document.createElement('div');
        outputConsoleDiv.className = 'block-output-console';
        const outputPre = document.createElement('pre');
        outputConsoleDiv.appendChild(outputPre);

        outputContainer.appendChild(outputHeader);
        outputContainer.appendChild(outputConsoleDiv);

        container.insertAdjacentElement('afterend', outputContainer);

        outputContainer.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
            const isHidden = outputConsoleDiv.classList.toggle('hidden');
            e.target.textContent = isHidden ? 'Show' : 'Hide';
        });
        outputContainer.querySelector('.copy-output-btn').addEventListener('click', async (e) => {
            try {
                await navigator.clipboard.writeText(outputPre.textContent || '');
                e.target.textContent = 'Copied!';
                setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
            } catch (err) { console.error('Failed to copy output:', err); }
        });
    } else {
        const outputPre = outputContainer.querySelector('.block-output-console pre, .block-output-console iframe');
        if (outputPre) {
            if (outputPre.tagName === 'IFRAME') {
                const newPre = document.createElement('pre');
                outputPre.parentNode.replaceChild(newPre, outputPre);
            } else {
                outputPre.textContent = '';
                outputPre.innerHTML = '';
                if (outputPre.htmlBuffer) {
                    delete outputPre.htmlBuffer;
                }
            }
        }
        const statusSpan = outputContainer.querySelector('.block-status');
        if (statusSpan) {
            statusSpan.textContent = 'Running...';
            statusSpan.className = 'code-status-span running';
        }
    }
    
    button.dataset.status = 'running';
    button.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
    button.title = 'Stop Execution';
    
    if (websocket && websocket.readyState === WebSocket.OPEN) {
        websocket.send(JSON.stringify({
            type: 'run_code',
            payload: { code_block_id: codeBlockId, language: language, code: code }
        }));
        
        setTimeout(() => {
            const outputBlock = document.getElementById(`output-for-${codeBlockId}`);
            if (outputBlock) {
                outputBlock.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        }, 100);
    } else {
        addErrorMessage("Cannot run code: Not connected to server.");
        button.dataset.status = 'idle';
        button.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
        button.title = 'Run Code';
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


function handleStructuredMessage(messageData) {
    const { type, payload } = messageData;
    console.log(`[handleStructuredMessage] Processing ${type} for ${payload?.code_block_id || 'unknown'}`);
    
    if (!payload || !payload.code_block_id) return;
    
    const codeBlockId = payload.code_block_id;
    const codeContainer = document.getElementById(codeBlockId);
    const outputBlockId = `output-for-${codeBlockId}`;
    let outputContainer = document.getElementById(outputBlockId);

    console.log(`[handleStructuredMessage] Found container for ${codeBlockId}:`, codeContainer);
    console.log(`[handleStructuredMessage] Found output container for ${outputBlockId}:`, outputContainer);

    if (!outputContainer && type === 'code_output') {
        console.log(`[DEBUG] Creating new output container for ${codeBlockId}`);
        outputContainer = document.createElement('div');
        outputContainer.id = outputBlockId;
        outputContainer.className = 'block-container';

        const blockNumber = codeBlockId.split('-').pop();
        console.log(`[DEBUG] Block number: ${blockNumber}`);

        const outputHeader = document.createElement('div');
        outputHeader.className = 'block-header';
        
        const headerHTML = createOutputHeaderHTML(blockNumber);
        console.log(`[DEBUG] Generated header HTML:`, headerHTML);
        outputHeader.innerHTML = headerHTML;
        console.log(`[DEBUG] Header after innerHTML:`, outputHeader);
        
        const outputConsoleDiv = document.createElement('div');
        outputConsoleDiv.className = 'block-output-console';
        const outputPre = document.createElement('pre');
        outputConsoleDiv.appendChild(outputPre);

        outputContainer.appendChild(outputHeader);
        outputContainer.appendChild(outputConsoleDiv);

        codeContainer.insertAdjacentElement('afterend', outputContainer);

        outputContainer.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
            const isHidden = outputConsoleDiv.classList.toggle('hidden');
            e.target.textContent = isHidden ? 'Show' : 'Hide';
        });
        outputContainer.querySelector('.copy-output-btn').addEventListener('click', async (e) => {
            try {
                await navigator.clipboard.writeText(outputPre.textContent || '');
                e.target.textContent = 'Copied!';
                setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
            } catch (err) { console.error('Failed to copy output:', err); }
        });
    } else if (outputContainer && type === 'code_output') {
        // Check if existing output container has proper header structure
        const existingStatusSpan = outputContainer.querySelector('.block-status');
        if (!existingStatusSpan) {
            console.log(`[DEBUG] Existing output container missing status span, fixing header...`);
            const existingHeader = outputContainer.querySelector('.block-header');
            if (existingHeader) {
                const blockNumber = codeBlockId.split('-').pop();
                const headerHTML = createOutputHeaderHTML(blockNumber);
                console.log(`[DEBUG] Replacing header HTML with:`, headerHTML);
                existingHeader.innerHTML = headerHTML;
                console.log(`[DEBUG] Header after fix:`, existingHeader);
                
                // Re-add event listeners
                outputContainer.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
                    const outputConsoleDiv = outputContainer.querySelector('.block-output-console');
                    const isHidden = outputConsoleDiv.classList.toggle('hidden');
                    e.target.textContent = isHidden ? 'Show' : 'Hide';
                });
                outputContainer.querySelector('.copy-output-btn').addEventListener('click', async (e) => {
                    try {
                        const outputPre = outputContainer.querySelector('.block-output-console pre');
                        await navigator.clipboard.writeText(outputPre.textContent || '');
                        e.target.textContent = 'Copied!';
                        setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
                    } catch (err) { console.error('Failed to copy output:', err); }
                });
            }
        }
    }

    if (!outputContainer || !codeContainer) return;
    
    switch (type) {
        case 'code_output': {
            const outputPre = outputContainer.querySelector('.block-output-console pre');
            const language = codeContainer.dataset.language;
            if (outputPre) {
                addCodeOutput(outputPre, payload.stream, payload.data, language);
            }
            break;
        }

        case 'code_waiting_input': {
            updateHeaderStatus(outputContainer, 'Waiting for input...', 'waiting-input');
            const outputPre = outputContainer.querySelector('.block-output-console pre');
            if (outputPre) {
                addTerminalPrompt(outputPre, payload.prompt || '');
            }
            break;
        }

        case 'code_finished': {
            console.log(`[handleStructuredMessage] Processing code_finished for ${codeBlockId}`);
            const runStopBtn = codeContainer.querySelector('.run-code-btn');
            const statusSpan = outputContainer.querySelector('.block-status');
            const outputConsoleDiv = outputContainer.querySelector('.block-output-console');
            const outputPre = outputConsoleDiv ? outputConsoleDiv.querySelector('pre') : null;

            console.log(`[handleStructuredMessage] Found run button:`, runStopBtn);
            console.log(`[handleStructuredMessage] Found status span:`, statusSpan);
            console.log(`[handleStructuredMessage] Found output console div:`, outputConsoleDiv);

            if (!runStopBtn || !statusSpan || !outputConsoleDiv || !outputPre) {
                console.log(`[handleStructuredMessage] Missing elements - runStopBtn: ${!!runStopBtn}, statusSpan: ${!!statusSpan}, outputConsoleDiv: ${!!outputConsoleDiv}, outputPre: ${!!outputPre}`);
                return;
            }

            const { exit_code, error } = payload;
            let finishMessage = '';
            let statusClass = '';
            
            const language = codeContainer.dataset.language;
            const codeElement = codeContainer.querySelector('code');
            const codeContent = codeElement ? codeElement.textContent || '' : '';
            
            let outputContent = null;
            let htmlContent = null;
            
            if (error) {
                finishMessage = 'Failed';
                statusClass = 'error';
                const helpfulHint = error.includes("Docker service is unavailable") 
                    ? "\n\nHint: Is Docker Desktop running?" 
                    : "";
                addCodeOutput(outputPre, 'stderr', `Error: ${error}${helpfulHint}`, language);
                outputContent = outputPre.textContent;
            } else if (language === 'html') {
                const iframe = document.createElement('iframe');
                iframe.className = 'html-render-iframe';
                iframe.style.width = '100%';
                iframe.style.border = '1px solid #e2e8f0';
                iframe.setAttribute('scrolling', 'no');
                iframe.style.overflow = 'hidden';
                iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
                
                htmlContent = outputPre.htmlBuffer || '';
                const style = `<style>body { margin: 0; background-color: white; color: black; font-family: sans-serif; padding: 1rem; }</style>`;
                iframe.srcdoc = style + htmlContent;
                
                iframe.onload = () => {
                    try {
                        const iWin = iframe.contentWindow;
                        const iDoc = iWin.document;
                        const updateHeight = () => {
                            if (!iDoc || !iDoc.body) return;
                            const newHeight = Math.max(
                                iDoc.body.scrollHeight, iDoc.documentElement.scrollHeight,
                                iDoc.body.offsetHeight, iDoc.documentElement.offsetHeight
                            );
                            iframe.style.height = `${newHeight}px`;
                        };
                        updateHeight();
                        const observer = new ResizeObserver(updateHeight);
                        if (iDoc.body) observer.observe(iDoc.body);
                        setTimeout(updateHeight, 150);
                        setTimeout(updateHeight, 500);
                    } catch (e) {
                        iframe.style.height = '400px';
                    }
                };
                
                outputPre.replaceWith(iframe);
                outputConsoleDiv.style.maxHeight = 'none';

                finishMessage = `Finished (Rendered HTML)`;
                statusClass = 'success';
            } else {
                finishMessage = `Finished (Exit: ${exit_code})`;
                statusClass = (exit_code === 0) ? 'success' : 'error';
                outputContent = outputPre.textContent;
            }
            
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                const codeBlockParts = codeBlockId.split('-');
                const extractedTurnId = codeBlockParts[2].replace('turn', '');
                
                websocket.send(JSON.stringify({
                    type: 'save_code_result',
                    payload: {
                        code_block_id: codeBlockId,
                        language: language,
                        code_content: codeContent,
                        output_content: outputContent,
                        html_content: htmlContent,
                        exit_code: exit_code,
                        error_message: error,
                        execution_status: error ? 'error' : 'completed',
                        turn_id: parseInt(extractedTurnId)
                    }
                }));
            }
            
            console.log(`[handleStructuredMessage] About to update button status to idle and set finish message: ${finishMessage}`);
            updateHeaderStatus(outputContainer, finishMessage, statusClass);
            runStopBtn.dataset.status = 'idle';
            runStopBtn.innerHTML = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
            runStopBtn.title = 'Run Code';
            console.log(`[handleStructuredMessage] Button and status updated successfully`);
            break;
        }
    }
}

function renderSingleMessage(msg, parentElement, isHistory = false, editedCodeBlocks = {}) {
    if (!parentElement || !msg) return;

    const senderType = msg.sender_type;
    const senderName = msg.sender_name || (senderType === 'ai' ? 'AI' : 'User');
    const timestamp = msg.timestamp;

    if (senderType === 'user' || senderType === 'system') {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message-item', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col');
        messageDiv.setAttribute('data-sender', senderType);
        if (msg.id) messageDiv.setAttribute('data-message-id', String(msg.id));

        if (senderType === 'user') {
            messageDiv.classList.add('bg-emerald-100', 'self-end', 'ml-auto');
        } else {
            messageDiv.classList.add('bg-slate-200', 'self-center', 'mx-auto', 'text-xs', 'italic');
        }

        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1');
        senderElem.classList.add(senderType === 'user' ? 'text-emerald-700' : 'text-slate-600');
        senderElem.textContent = escapeHTML(senderName);
        messageDiv.appendChild(senderElem);

        const contentElem = document.createElement('div');
        contentElem.classList.add('text-gray-800', 'text-sm', 'message-content');
        let displayedContent = msg.content || '';
        if (senderType === 'user' && displayedContent.startsWith(NO_THINK_PREFIX)) {
            displayedContent = displayedContent.substring(NO_THINK_PREFIX.length);
        }
        contentElem.innerHTML = marked.parse(displayedContent);
        messageDiv.appendChild(contentElem);

        if (timestamp) {
            const timestampElem = document.createElement('p');
            timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1');
            timestampElem.classList.add(senderType === 'user' ? 'text-right' : 'text-center');
            try {
                timestampElem.textContent = new Date(timestamp).toLocaleString();
            } catch (e) {
                timestampElem.textContent = String(timestamp);
            }
            messageDiv.appendChild(timestampElem);
        }
        parentElement.appendChild(messageDiv);

    } else if (senderType === 'ai') {
        const turnIdSuffix = (msg.turn_id !== null && msg.turn_id !== undefined) ? String(msg.turn_id) : (msg.id ? `msg${msg.id}` : `hist-${Date.now()}`);

        const aiTurnContainer = document.createElement('div');
        aiTurnContainer.classList.add('ai-turn-container');
        if (msg.id) aiTurnContainer.setAttribute('data-message-id', String(msg.id));

        const thinkingArea = document.createElement('div');
        thinkingArea.classList.add('thinking-area');
        thinkingArea.style.display = msg.thinking_content ? 'block' : 'none';

        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.classList.add('thinking-summary');
        summary.innerHTML = `<span class="text">Show Thinking</span><span class="dots"></span>`;
        const thinkingPre = document.createElement('pre');
        thinkingPre.textContent = msg.thinking_content || '';
        details.appendChild(summary);
        details.appendChild(thinkingPre);
        thinkingArea.appendChild(details);

        const answerElement = document.createElement('div');
        answerElement.classList.add('message', 'ai-message', 'p-3', 'rounded-lg', 'max-w-xl', 'mb-2', 'break-words', 'flex', 'flex-col', 'bg-sky-100', 'self-start', 'mr-auto');

        const senderElem = document.createElement('p');
        senderElem.classList.add('font-semibold', 'text-sm', 'mb-1', 'text-sky-700');
        senderElem.textContent = 'AI';
        answerElement.appendChild(senderElem);

        const contentDiv = document.createElement('div');
        contentDiv.classList.add('text-gray-800', 'text-sm', 'message-content');
        answerElement.appendChild(contentDiv);

        const codeBlocksArea = document.createElement('div');
        codeBlocksArea.classList.add('code-blocks-area');

        parseAndRenderAiContent(msg.content, contentDiv, codeBlocksArea, turnIdSuffix, editedCodeBlocks);

        if (timestamp) {
            const timestampElem = document.createElement('p');
            timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-left', 'timestamp-p');
            timestampElem.textContent = new Date(timestamp).toLocaleString();
            answerElement.appendChild(timestampElem);
        }

        aiTurnContainer.appendChild(thinkingArea);
        aiTurnContainer.appendChild(answerElement);
        aiTurnContainer.appendChild(codeBlocksArea);
        parentElement.appendChild(aiTurnContainer);
    }
}

function restoreCodeExecutionResult(result) {
    const codeContainer = document.getElementById(result.code_block_id);
    if (!codeContainer) {
        console.log(`Code container not found for ${result.code_block_id}, skipping restore`);
        return;
    }

    const outputBlockId = `output-for-${result.code_block_id}`;
    let outputContainer = document.getElementById(outputBlockId);
    
    if (outputContainer) {
        outputContainer.remove();
    }

    outputContainer = document.createElement('div');
    outputContainer.id = outputBlockId;
    outputContainer.className = 'block-container';

    const blockNumber = result.code_block_id.split('-').pop();

    const outputHeader = document.createElement('div');
    outputHeader.className = 'block-header';
    
    let statusText = '';
    let statusClass = '';
    if (result.error_message) {
        statusText = 'Failed';
        statusClass = 'error';
    } else if (result.language === 'html') {
        statusText = 'Finished (Rendered HTML)';
        statusClass = 'success';
    } else {
        statusText = `Finished (Exit: ${result.exit_code})`;
        statusClass = (result.exit_code === 0) ? 'success' : 'error';
    }
    
    outputHeader.innerHTML = `
        <div class="block-buttons">
            <button class="toggle-output-btn block-action-btn">Hide</button>
            <button class="copy-output-btn block-action-btn">Copy</button>
        </div>
        <span class="block-title">Output Block ${blockNumber}</span>
        <span class="block-status ${statusClass}">${statusText}</span>
    `;
    
    const outputConsoleDiv = document.createElement('div');
    outputConsoleDiv.className = 'block-output-console';
    
    if (result.language === 'html' && result.html_content) {
        const iframe = document.createElement('iframe');
        iframe.className = 'html-render-iframe';
        iframe.style.width = '100%';
        iframe.style.border = '1px solid #e2e8f0';
        iframe.setAttribute('scrolling', 'no');
        iframe.style.overflow = 'hidden';
        iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin');
        
        const style = `<style>body { margin: 0; background-color: white; color: black; font-family: sans-serif; padding: 1rem; }</style>`;
        iframe.srcdoc = style + result.html_content;
        
        iframe.onload = () => {
            try {
                const iWin = iframe.contentWindow;
                const iDoc = iWin.document;
                const updateHeight = () => {
                    if (!iDoc || !iDoc.body) return;
                    const newHeight = Math.max(
                        iDoc.body.scrollHeight, iDoc.documentElement.scrollHeight,
                        iDoc.body.offsetHeight, iDoc.documentElement.offsetHeight
                    );
                    iframe.style.height = `${newHeight}px`;
                };
                updateHeight();
                const observer = new ResizeObserver(updateHeight);
                if (iDoc.body) observer.observe(iDoc.body);
                setTimeout(updateHeight, 150);
                setTimeout(updateHeight, 500);
            } catch (e) {
                iframe.style.height = '400px';
            }
        };
        
        outputConsoleDiv.appendChild(iframe);
        outputConsoleDiv.style.maxHeight = 'none';
    } else {
        const outputPre = document.createElement('pre');
        if (result.output_content) {
            outputPre.textContent = result.output_content;
        }
        outputConsoleDiv.appendChild(outputPre);
    }

    outputContainer.appendChild(outputHeader);
    outputContainer.appendChild(outputConsoleDiv);

    codeContainer.insertAdjacentElement('afterend', outputContainer);

    outputContainer.querySelector('.toggle-output-btn').addEventListener('click', (e) => {
        const isHidden = outputConsoleDiv.classList.toggle('hidden');
        e.target.textContent = isHidden ? 'Show' : 'Hide';
    });
    
    const copyBtn = outputContainer.querySelector('.copy-output-btn');
    copyBtn.addEventListener('click', async (e) => {
        try {
            const textToCopy = result.language === 'html' && result.html_content ? 
                result.html_content : (result.output_content || '');
            await navigator.clipboard.writeText(textToCopy);
            e.target.textContent = 'Copied!';
            setTimeout(() => { e.target.textContent = 'Copy'; }, 1500);
        } catch (err) { 
            console.error('Failed to copy output:', err); 
        }
    });
}

async function loadAndDisplayChatHistory(sessionId) {
    const chatHistoryDiv = document.getElementById('chat-history');
    if (!chatHistoryDiv) {
        console.error("Chat history container 'chat-history' not found.");
        return;
    }

    streamingCodeBlockCounter = 0;
    chatHistoryDiv.innerHTML = '<p class="text-center text-gray-500 p-4">Loading history...</p>';

    try {
        // Fetch all data concurrently
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
                // Pass the fetched data down to the rendering function
                renderSingleMessage(msg, chatHistoryDiv, true, editedCodeBlocks);
            });

            codeResults.forEach(result => {
                restoreCodeExecutionResult(result);
            });

            if (typeof Prism !== 'undefined' && typeof Prism.highlightAll === 'function') {
                Prism.highlightAll();
            }

            chatHistoryDiv.scrollTop = chatHistoryDiv.scrollHeight;
        }
        console.log(`Successfully loaded ${messages.length} messages for session ${sessionId}.`);

    } catch (error){
        console.error(`Failed to fetch or display chat history for session ${sessionId}:`, error);
        chatHistoryDiv.innerHTML = `<p class="text-center text-red-500 p-4">An unexpected error occurred while loading history: ${escapeHTML(error.message)}</p>`;
    }
}
// --- WebSocket Connection ---
function resetAllCodeButtonsOnErrorOrClose() {
    console.log("Resetting all code run/stop buttons and statuses due to connection issue.");
    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    document.querySelectorAll('.block-container').forEach(container => {
        const button = container.querySelector('.run-code-btn');
        const outputHeader = container.querySelector('.block-header');
        const statusSpan = outputHeader ? outputHeader.querySelector('.block-status') : null;

        if (button && button.dataset.status !== 'idle') {
            button.dataset.status = 'idle';
            button.innerHTML = playIconSvg;
            button.title = 'Run Code';
            button.disabled = false;
        }
        if (statusSpan) {
            if (!statusSpan.classList.contains('idle') && !statusSpan.classList.contains('success')) {
                if (outputHeader) outputHeader.style.display = 'flex';
                statusSpan.textContent = 'Error: Disconnected';
                statusSpan.className = 'code-status-span error';
            }
        }
    });
}

// ADD THIS ENTIRE BLOCK OF HELPER FUNCTIONS

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
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

function finalizeTurnOnErrorOrClose() {
    if (currentAnswerElement && !currentAnswerElement.querySelector('.timestamp-p')) {
        const timestampElem = document.createElement('p');
        timestampElem.classList.add('text-xs', 'text-slate-500', 'mt-1', 'text-left', 'timestamp-p');
        timestampElem.textContent = new Date().toLocaleString();
        currentAnswerElement.appendChild(timestampElem);
    }
    
    // Clean up streaming dots from any active code blocks
    activeStreamingCodeBlocks.forEach((blockData) => {
        const container = document.getElementById(blockData.blockId);
        if (container) {
            const dotsSpan = container.querySelector('.streaming-dots');
            if (dotsSpan) dotsSpan.remove();
        }
    });
    
    // Reset state
    codeBlockCounterThisTurn = 0;
    accumulatedAnswerText = '';
    hasThinkingContentArrivedThisTurn = false;
    firstAnswerTokenReceived = false;
    thinkingRequestedForCurrentTurn = false;
    activeStreamingCodeBlocks.clear();
    streamingCodeBlockCounter = 0;
    
    setInputDisabledState(false, false);
    if (messageInput && messageInput.offsetParent !== null) {
        messageInput.focus();
    }
}

function connectWebSocket() {
    let sessionId = getSessionIdFromPath();
    if (!sessionId) {
        addErrorMessage("Cannot connect to chat: Invalid session ID in URL.");
        setInputDisabledState(true);
        return;
    }
    
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${sessionId}/${clientId}`;
    console.log(`[WebSocket] Attempting to connect: ${wsUrl}`);

    try {
        const ws = new WebSocket(wsUrl);
        websocket = ws;

        ws.onopen = () => {
            console.log("[WebSocket] Connection opened.");
            setInputDisabledState(false, false);
            addSystemMessage("Connected to the chat server.");
            updateAllRunButtonStates();
            if (messageInput) messageInput.focus();
        };

        ws.onclose = (event) => {
            if (window.isNavigatingAway) return;
            console.log("WebSocket connection closed.", event);
            addSystemMessage(`Connection closed: ${event.reason || 'Normal closure'} (Code: ${event.code})`);
            finalizeTurnOnErrorOrClose();
            resetAllCodeButtonsOnErrorOrClose();
            updateAllRunButtonStates();
            const noReconnectCodes = [1000, 1005, 1008, 1011];
            if (!noReconnectCodes.includes(event.code)) {
                addSystemMessage("Attempting to reconnect...");
                setInputDisabledState(true, false);
                setTimeout(connectWebSocket, 3000);
            } else {
                setInputDisabledState(true, false);
            }
        };

        ws.onerror = (event) => {
            console.error("WebSocket error:", event);
            addErrorMessage("WebSocket connection error. Please try refreshing.");
            finalizeTurnOnErrorOrClose();
            resetAllCodeButtonsOnErrorOrClose();
            updateAllRunButtonStates();
            setInputDisabledState(true, false);
        };

        ws.onmessage = (event) => {
            let messageData;
            try {
                messageData = JSON.parse(event.data);
                console.log("[WebSocket] Received structured message:", messageData); // ADD THIS
            } catch (e) {
                messageData = null;
            }

            if (messageData && messageData.type) {
                console.log("[WebSocket] Handling structured message type:", messageData.type); // ADD THIS
                handleStructuredMessage(messageData);
            } else {
                const chunk = event.data;
                if (chunk === "<EOS>" || chunk === "<EOS_STOPPED>") {
                    console.log(`%c[WebSocket] Received ${chunk}. Finalizing turn.`, 'color: green; font-weight: bold;');
                    renderLiveMessage(accumulatedAnswerText);
                    finalizeTurnOnErrorOrClose();
                    return;
                }
                if (chunk.startsWith("<ERROR>")) {
                    addErrorMessage(chunk.substring(7));
                    finalizeTurnOnErrorOrClose();
                    return;
                }
                if (!currentAiTurnContainer && chunk.trim().length > 0) {
                    setupNewAiTurn();
                }
                accumulatedAnswerText += chunk;
                renderLiveMessage(accumulatedAnswerText);
            }
        };
    } catch (error) {
        console.error("WebSocket creation error:", error);
        addErrorMessage(`WebSocket Creation Error: ${error.message}.`);
        setInputDisabledState(true, false);
        updateAllRunButtonStates();
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    await loadLanguagesConfig();
    await initializeCurrentUser();

    setInputDisabledState(true, false);

    if (typeof marked !== 'undefined' && typeof marked.setOptions === 'function') {
        marked.setOptions({
            gfm: true, breaks: true, sanitize: false, smartLists: true, smartypants: false,
        });
    }

    if (chatForm && messageInput && sendButton && stopAiButton) {
        chatForm.addEventListener('submit', (event) => {
            event.preventDefault();
            const userMessage = messageInput.value.trim();
            if (!userMessage) return;
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                addErrorMessage("Not connected to the server.");
                return;
            }
            
            try {
                addUserMessage(userMessage);
                thinkingRequestedForCurrentTurn = thinkCheckbox ? thinkCheckbox.checked : false;
                let messageTextForPayload;
                if (thinkingRequestedForCurrentTurn) {
                    messageTextForPayload = THINK_PREFIX + userMessage.replace(NO_THINK_PREFIX, '').replace(THINK_PREFIX, '');
                } else {
                    messageTextForPayload = NO_THINK_PREFIX + userMessage.replace(NO_THINK_PREFIX, '').replace(THINK_PREFIX, '');
                }
                
                setupNewAiTurn();
                
                const messagePayload = {
                    type: "chat_message",
                    payload: {
                        user_input: messageTextForPayload,
                        turn_id: currentTurnId 
                    }
                };
                websocket.send(JSON.stringify(messagePayload));
                
                messageInput.value = '';
                setInputDisabledState(true, true);
            } catch (sendError) {
                addErrorMessage(`Failed to send message: ${sendError.message}`);
            }
        });

        if (chatHistory) {
            chatHistory.addEventListener('click', (event) => {
                const target = event.target.closest('a.code-block-link');
                if (!target) {
                    return;
                }

                event.preventDefault();
                const targetId = target.getAttribute('href').substring(1);
                const targetElement = document.getElementById(targetId);

                if (targetElement) {
                    targetElement.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        }

        stopAiButton.addEventListener('click', () => {
            if (!websocket || websocket.readyState !== WebSocket.OPEN) {
                addErrorMessage("Cannot stop: Not connected.");
                return;
            }
            const sessionId = getSessionIdFromPath();
            if (!sessionId) {
                addErrorMessage("Cannot stop: Session ID not found.");
                return;
            }
            websocket.send(JSON.stringify({
                type: "stop_ai_stream",
                payload: {
                    client_id: clientId,
                    session_id: sessionId,
                    turn_id: currentTurnId 
                }
            }));
            stopAiButton.disabled = true;
            finalizeTurnOnErrorOrClose();
        });

    } else {
        if (window.location.pathname.includes("/chat/")) {
            addErrorMessage("Initialization Error: Chat input components missing.");
        }
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
                        
                        // Handle clipping when approaching the bottom of content
                        const stickyId = m.header.dataset.stickyId;
                        if (stickyId) {
                            const stickyClone = document.getElementById(stickyId);
                            if (stickyClone) {
                                const headerHeight = stickyClone.offsetHeight;
                                const contentBottom = m.contentRect.bottom;
                                const viewportTop = scrollerRect.top + 1; // Add 1px offset
                                
                                // Calculate how much space is available for the header (add 2px buffer)
                                const availableSpace = contentBottom - viewportTop + 3;
                                
                                if (availableSpace < headerHeight && availableSpace > 0) {
                                    // Clip the header from the top by moving it up and adjusting height
                                    const clipAmount = headerHeight - availableSpace - 1;
                                    stickyClone.style.top = `${viewportTop - clipAmount - 1}px`; // Move up by clip amount
                                    stickyClone.style.clipPath = `inset(${clipAmount}px 0 0 0)`; // Clip from top
                                    stickyClone.style.height = `${headerHeight}px`; // Keep original height for proper clipping
                                    stickyClone.style.borderBottomLeftRadius = '0.375rem';
                                    stickyClone.style.borderBottomRightRadius = '0.375rem';
                                } else if (availableSpace <= 2) {
                                    // Content has scrolled completely past, hide the sticky header
                                    stickyClone.style.display = 'none';
                                } else {
                                    // Normal case: full header visible
                                    stickyClone.style.height = 'auto';
                                    stickyClone.style.top = `${scrollerRect.top}px`; // Use original viewport top without offset
                                    stickyClone.style.display = 'flex';
                                    stickyClone.style.clipPath = 'none'; // Reset clipping
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
            // Only handle scroll if it's specifically from the chat history scroller
            if (e.target === chatHistoryScroller) {
                handleScroll();
            }
        }, { passive: true });
            }
});