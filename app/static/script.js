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
// import 'prismjs/components/prism-php'; // not working
// import 'prismjs/components/prism-ruby'; // not working
// --------------------------

// --- Constants and Variables ---
const chatHistory = document.getElementById('chat-history');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');
const sendButton = document.getElementById('send-button');
const thinkCheckbox = document.getElementById('think-checkbox');

let websocket;
const clientId = `web-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`;
let currentTurnId = 0;

const MODE_ANSWER = 'MODE_ANSWER';
const MODE_SEEKING_CODE_LANGUAGE = 'MODE_SEEKING_CODE_LANGUAGE';
const MODE_INSIDE_CODE_BLOCK = 'MODE_INSIDE_CODE_BLOCK';
const MODE_KATEX_BUFFERING_INLINE = 'MODE_KATEX_BUFFERING_INLINE';
const MODE_KATEX_BUFFERING_DISPLAY = 'MODE_KATEX_BUFFERING_DISPLAY';
const MODE_MAYBE_START_DISPLAY_KATEX = 'MODE_MAYBE_START_DISPLAY_KATEX';
const MODE_SEEKING_TAG = 'MODE_SEEKING_TAG';
const MODE_INSIDE_THINK = 'MODE_INSIDE_THINK';
const MODE_MAYBE_END_THINK = 'MODE_MAYBE_END_THINK';
const NO_THINK_PREFIX = "\\no_think";
const MODE_SEEKING_CODE_START_FENCE = 'MODE_SEEKING_CODE_START_FENCE';
const MODE_SEEKING_CODE_END_FENCE = 'MODE_SEEKING_CODE_END_FENCE';

let currentProcessingMode = MODE_ANSWER;
let langBuffer = '';
let currentCodeBlockLang = ''; // Stores the language name (e.g., 'python')
let currentCodeBlockElement = null; // The <code> element
let currentCodeBlockPreElement = null; // The <pre> element wrapping the code
let katexBuffer = '';
let currentKatexMarkerId = null;
let thinkBuffer = '';
let tagBuffer = '';
let fenceBuffer = ''; // Added for fence detection
let currentAiTurnContainer = null;
let currentAnswerElement = null;
let currentThinkingArea = null;
let currentThinkingPreElement = null;
let currentCodeBlocksArea = null;
let codeBlockCounterThisTurn = 0;
let thinkingRequestedForCurrentTurn = false;
let accumulatedAnswerText = '';
let lastAppendedNode = null;
let hasThinkingContentArrivedThisTurn = false;
let firstAnswerTokenReceived = false;

const FENCE = '```';
const THINK_START_TAG = '<think>';
const THINK_END_TAG = '</think>';
const KATEX_PLACEHOLDER_PREFIX = '%%KATEX_PLACEHOLDER_';
const KATEX_RENDERED_ATTR = 'data-katex-rendered';


// --- Utility Functions ---

function scrollToBottom(behavior = 'auto') {
    const isNearBottom = chatHistory.scrollHeight - chatHistory.scrollTop - chatHistory.clientHeight < 100;
    if (isNearBottom) {
        requestAnimationFrame(() => {
            chatHistory.scrollTo({ top: chatHistory.scrollHeight, behavior: behavior });
        });
    }
}

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
};

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
    } catch (e) {
        console.error("Error setting preSelectionRange end:", e, "Range:", range);
        return -1;
    }
    return preSelectionRange.toString().length;
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
                    const offsetInNode = offset - charCount;
                    range.setStart(node, offsetInNode);
                    foundStart = true;
                } catch (e) {
                    console.error("Error setting range start:", e, "Node:", node, "Offset:", offsetInNode);
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
        console.warn(`Could not set cursor precisely at offset ${offset}. Placed at end.`);
    }
    parentElement.focus();
}

// --- Message Display Functions ---

function addUserMessage(text) {
    const messageElement = document.createElement('div');
    messageElement.classList.add('message', 'user-message');
    messageElement.textContent = text;
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
   if(currentAiTurnContainer) {
       const target = currentAnswerElement || currentCodeBlocksArea || currentAiTurnContainer;
       target.appendChild(messageElement);
   } else { chatHistory.appendChild(messageElement); }
   setTimeout(() => scrollToBottom('smooth'), 50);
}

function setInputDisabledState(disabled) {
    messageInput.disabled = disabled;
    sendButton.disabled = disabled;
    if (thinkCheckbox) {
        thinkCheckbox.disabled = disabled;
    }
}

function setupNewAiTurn() {
    currentTurnId++;
    codeBlockCounterThisTurn = 0;
    accumulatedAnswerText = '';
    lastAppendedNode = null;
    thinkBuffer = '';
    hasThinkingContentArrivedThisTurn = false;
    firstAnswerTokenReceived = false;

    currentAiTurnContainer = null;
    currentThinkingArea = null;
    currentThinkingPreElement = null;
    currentAnswerElement = null;
    currentCodeBlocksArea = null;

    console.log(`[setupNewAiTurn] Starting setup for Turn ID: ${currentTurnId}. thinkingRequestedForCurrentTurn is: ${thinkingRequestedForCurrentTurn}`);

    currentAiTurnContainer = document.createElement('div');
    currentAiTurnContainer.classList.add('ai-turn-container');
    currentAiTurnContainer.dataset.turnId = currentTurnId;

    currentThinkingArea = document.createElement('div');
    currentThinkingArea.classList.add('thinking-area');
    currentThinkingArea.dataset.turnId = currentTurnId;

    if (!thinkingRequestedForCurrentTurn) {
        console.log("[setupNewAiTurn] Setting thinking area style to display:none because thinkingRequested=false.");
        currentThinkingArea.style.display = 'none';
    } else {
        console.log("[setupNewAiTurn] NOT hiding thinking area initially because thinkingRequested=true.");
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

    currentAnswerElement = document.createElement('div');
    currentAnswerElement.classList.add('message', 'ai-message');

    if (thinkingRequestedForCurrentTurn) {
        console.log("[setupNewAiTurn] Hiding answer bubble initially because thinkingRequested=true.");
        currentAnswerElement.style.display = 'none';
    } else {
        console.log("[setupNewAiTurn] Showing answer bubble immediately with loading dots because thinkingRequested=false.");
         const loadingSpan = document.createElement('span');
         loadingSpan.classList.add('loading-dots');
         currentAnswerElement.appendChild(loadingSpan);
    }
    currentAiTurnContainer.appendChild(currentAnswerElement);

    currentCodeBlocksArea = document.createElement('div');
    currentCodeBlocksArea.classList.add('code-blocks-area');
    currentAiTurnContainer.appendChild(currentCodeBlocksArea);

    chatHistory.appendChild(currentAiTurnContainer);

    console.log(`[setupNewAiTurn] Finished setup for Turn ID: ${currentTurnId}.`);
}

function appendRawTextToThinkingArea(text) {
    if (text && text.trim().length > 0) {
        // console.log(`[appendRawTextToThinkingArea] Received non-empty raw think text: "${text}" (length: ${text?.length})`);
    } else if (text !== null) {
        // console.log(`[appendRawTextToThinkingArea] Received empty or whitespace raw think text (length: ${text?.length})`);
    }

    // console.log(`[appendRawTextToThinkingArea] Checking condition: !thinkingRequestedForCurrentTurn is ${!thinkingRequestedForCurrentTurn} (value: ${thinkingRequestedForCurrentTurn})`);
    if (!thinkingRequestedForCurrentTurn) {
        // console.log("[appendRawTextToThinkingArea] Condition met (!thinkingRequested). Returning early. Should NOT display thinking.");
        return;
    }

    // console.log("[appendRawTextToThinkingArea] Proceeding because thinking was requested.");

    if (!currentThinkingArea || !currentThinkingPreElement || text.length === 0) {
        return;
    }

    if (!hasThinkingContentArrivedThisTurn) {
        if (currentThinkingArea.style.display === 'none') {
            // console.log("[appendRawTextToThinkingArea] First think chunk (and requested). Making area visible NOW. Display style was: " + currentThinkingArea.style.display);
            currentThinkingArea.style.display = '';
        }
        hasThinkingContentArrivedThisTurn = true;
    }

    try {
        currentThinkingPreElement.appendChild(document.createTextNode(text));
    } catch (appendError) {
         console.error("[appendRawTextToThinkingArea] Error appending text node:", appendError, "Pre Element:", currentThinkingPreElement, "Text:", text);
        return;
    }

    const detailsElement = currentThinkingPreElement.closest('details');
    if (detailsElement && detailsElement.open) {
         const isNearBottom = currentThinkingPreElement.scrollHeight - currentThinkingPreElement.scrollTop - currentThinkingPreElement.clientHeight < 50;
         if(isNearBottom) {
             requestAnimationFrame(() => { currentThinkingPreElement.scrollTop = currentThinkingPreElement.scrollHeight; });
         }
    }
}

function appendCodeReference() {
    if (!currentAnswerElement) {
        console.error("Attempted to append code reference to null answer bubble!");
        return;
    }
    if (codeBlockCounterThisTurn > 0) {
         const refSpan = document.createElement('span');
         refSpan.classList.add('code-reference');
         refSpan.textContent = `[Code ${codeBlockCounterThisTurn}]`;
         currentAnswerElement.appendChild(refSpan);
         lastAppendedNode = refSpan;
    } else {
        console.warn("appendCodeReference called but codeBlockCounterThisTurn is 0.");
    }
}

function formatAnswerBubbleFinal() {
    if (!currentAnswerElement) {
        console.warn("[DEBUG] Skipping final formatting: currentAnswerElement is null.");
        accumulatedAnswerText = ''; lastAppendedNode = null; firstAnswerTokenReceived = false; return;
    }

     if (currentAnswerElement.style.display === 'none' && (currentAnswerElement.hasChildNodes() || accumulatedAnswerText.trim().length > 0)) {
         console.warn("[formatAnswerBubbleFinal] Answer bubble was hidden but contained content. Making visible.");
         currentAnswerElement.style.display = '';
          const loadingDots = currentAnswerElement.querySelector('.loading-dots');
          if (loadingDots) loadingDots.remove();
     }

    const hasVisualContent = currentAnswerElement.hasChildNodes() && !currentAnswerElement.querySelector('.loading-dots');
    const hasAccumulatedContent = accumulatedAnswerText.trim().length > 0;

    if (!hasVisualContent && !hasAccumulatedContent) {
        console.log("[DEBUG] Skipping final formatting: No actual content found.");
         const loadingDots = currentAnswerElement.querySelector('.loading-dots');
         if (loadingDots) loadingDots.remove();
        accumulatedAnswerText = '';
        lastAppendedNode = null;
        return;
    }

    // console.log(`[DEBUG formatAnswerBubbleFinal] Proceeding. Has Visual: ${hasVisualContent}, Has Accumulated: ${hasAccumulatedContent}`);

    try {
        const storedKatexNodes = {};
        let placeholderIndex = 0;
        let textForMarkdown = accumulatedAnswerText;

        const katexSpans = Array.from(currentAnswerElement.children).filter(el => el.matches(`span[${KATEX_RENDERED_ATTR}="true"]`));

        if (katexSpans.length > 0) {
             katexSpans.forEach((el) => {
                 if (!el.parentNode) return;
                 const placeholder = `${KATEX_PLACEHOLDER_PREFIX}${placeholderIndex++}`;
                 storedKatexNodes[placeholder] = el.cloneNode(true);
                 try {
                     el.parentNode.replaceChild(document.createTextNode(placeholder), el);
                 } catch (replaceError) {
                     console.error(`[DEBUG] Error replacing KaTeX node with placeholder ${placeholder}:`, replaceError, "Node:", el);
                     try { el.parentNode.removeChild(el); } catch(removeError) { console.error("Failed to remove problematic KaTeX node:", removeError); }
                 }
             });
             textForMarkdown = currentAnswerElement.innerHTML;
        } else {
             if (hasVisualContent && hasAccumulatedContent) {
                 // console.log("[DEBUG formatAnswerBubbleFinal] No KaTeX found, clearing visual DOM and using accumulated text for Markdown.");
                 currentAnswerElement.innerHTML = '';
             } else if (!hasAccumulatedContent && hasVisualContent) {
                 // console.log("[DEBUG formatAnswerBubbleFinal] No KaTeX found, using existing innerHTML for Markdown.");
                 textForMarkdown = currentAnswerElement.innerHTML;
             }
        }

         if (textForMarkdown.trim().length === 0) {
            // console.log("[DEBUG formatAnswerBubbleFinal] textForMarkdown is empty, skipping marked parse.");
         } else {
             const markdownHtml = marked.parse(textForMarkdown, {
                 mangle: false, headerIds: false, gfm: true, breaks: true, sanitize: false
             });
             currentAnswerElement.innerHTML = markdownHtml;
             lastAppendedNode = null;
         }

         if (Object.keys(storedKatexNodes).length > 0) {
             // console.log("[DEBUG formatAnswerBubbleFinal] Reinserting KaTeX nodes.");
             const walker = document.createTreeWalker(currentAnswerElement, NodeFilter.SHOW_TEXT);
             let node;
             const textNodesContainingPlaceholders = [];
             while (node = walker.nextNode()) {
                 if (node.nodeValue && node.nodeValue.includes(KATEX_PLACEHOLDER_PREFIX)) {
                     textNodesContainingPlaceholders.push(node);
                 }
             }

             textNodesContainingPlaceholders.forEach(textNode => {
                 let currentNodeValue = textNode.nodeValue;
                 let parent = textNode.parentNode;
                 if (!parent) return;
                 let lastNodeInserted = textNode;
                 for (const placeholder in storedKatexNodes) {
                      if (currentNodeValue.includes(placeholder)) {
                           const parts = currentNodeValue.split(placeholder);
                           let firstPart = parts.shift();
                           if (firstPart) {
                               parent.insertBefore(document.createTextNode(firstPart), lastNodeInserted);
                           }
                           const katexNode = storedKatexNodes[placeholder].cloneNode(true);
                           parent.insertBefore(katexNode, lastNodeInserted);
                           currentNodeValue = parts.join(placeholder);
                       }
                 }
                 if (currentNodeValue) {
                     parent.insertBefore(document.createTextNode(currentNodeValue), lastNodeInserted);
                 }
                 parent.removeChild(lastNodeInserted);
             });
         } else {
            // console.log("[DEBUG formatAnswerBubbleFinal] No KaTeX nodes to reinsert.");
         }

    } catch (error) {
        console.error("Error during final Markdown/KaTeX formatting:", error);
        addErrorMessage("Failed to perform final message formatting.");
        if (currentAnswerElement && accumulatedAnswerText.trim().length > 0) {
             console.warn("Attempting fallback to raw accumulated text due to formatting error.");
             currentAnswerElement.textContent = accumulatedAnswerText;
        }
    }
    accumulatedAnswerText = '';
}

function resetStreamingState() {
    // console.log("[DEBUG] Resetting streaming state.");
    currentProcessingMode = MODE_ANSWER;
    langBuffer = ''; currentCodeBlockLang = '';
    currentCodeBlockElement = null; currentCodeBlockPreElement = null;
    katexBuffer = ''; currentKatexMarkerId = null;
    thinkBuffer = ''; tagBuffer = ''; fenceBuffer = ''; // Reset fenceBuffer
    lastAppendedNode = null;
    thinkingRequestedForCurrentTurn = false;
}

function renderAndReplaceKatex(isDisplay, markerId) {

    if (!currentAnswerElement || !markerId || katexBuffer === null || katexBuffer === undefined) {
        console.error("[KaTeX Replace Marker] Error: Invalid state (element, markerId, or buffer). MarkerID:", markerId, "Buffer:", katexBuffer);
        if (markerId && currentAnswerElement) {
             try {
                 const strayMarker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);
                 if (strayMarker?.parentNode) strayMarker.parentNode.removeChild(strayMarker);
             } catch (e) { console.error("Error cleaning stray marker:", e); }
        }
        katexBuffer = '';
        return false;
    }

    const trimmedKatexBuffer = katexBuffer.trim();

    try {
        const stringToRender = trimmedKatexBuffer.length > 0 ? trimmedKatexBuffer : " ";
        const katexHtml = katex.renderToString(stringToRender, {
            displayMode: isDisplay,
            throwOnError: false,
            output: "html",
            strict: false
        });

        if (katexHtml.includes('katex-error') && trimmedKatexBuffer.length > 0) {
            console.warn(`[KaTeX Replace Marker] KaTeX reported a rendering error for buffer: "${trimmedKatexBuffer}". Marker ID: ${markerId}. Raw text will remain.`);
             try {
                 const marker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);
                 if (marker?.parentNode) marker.parentNode.removeChild(marker);
                 else console.warn("Couldn't find marker to clean up after render error.");
             } catch (cleanupError) { console.error("Error during marker cleanup after render error:", cleanupError); }
            katexBuffer = '';
            return false;
        }

        const startMarker = currentAnswerElement.querySelector(`span[data-katex-start-id="${markerId}"]`);

        if (!startMarker) {
            console.error(`[KaTeX Replace Marker] Error: Cannot find start marker span with ID: ${markerId}. Aborting replacement. Content might be inconsistent.`);
            katexBuffer = '';
            return false;
        }

        if (!startMarker.parentNode) {
             console.error(`[KaTeX Replace Marker] Error: Start marker span with ID ${markerId} found but has no parent (already removed?). Aborting.`);
             katexBuffer = '';
             return false;
        }

        const parent = startMarker.parentNode;
        let nodesToRemove = [];
        let currentNode = startMarker.nextSibling;

        while (currentNode) {
            nodesToRemove.push(currentNode);
            if (nodesToRemove.length > 1000) {
                console.error("[KaTeX Replace Marker] Error: Excessive node collection limit reached (1000). Aborting replacement.");
                 try { parent.removeChild(startMarker); } catch(e){}
                 katexBuffer = '';
                 return false;
            }
            currentNode = currentNode.nextSibling;
        }

        const katexSpan = document.createElement('span');
        katexSpan.setAttribute(KATEX_RENDERED_ATTR, 'true');
        katexSpan.innerHTML = katexHtml;

        parent.insertBefore(katexSpan, startMarker);
        parent.removeChild(startMarker);

        nodesToRemove.forEach(node => {
            if (node.parentNode === parent) {
                parent.removeChild(node);
            } else {
                console.warn("[KaTeX Replace Marker] Node parent changed or node removed unexpectedly during collection, skipping removal:", node);
            }
        });

        appendToAnswer(null, katexSpan);
        katexBuffer = '';
        return true;

    } catch (error) {
        console.error("[KaTeX Replace Marker] General error during marker-based replacement:", error);
        try {
            const marker = currentAnswerElement?.querySelector(`span[data-katex-start-id="${markerId}"]`);
            if (marker?.parentNode) marker.parentNode.removeChild(marker);
        } catch (cleanupError) { console.error("Error during marker cleanup after general error:", cleanupError); }
        katexBuffer = '';
        return false;
    }
}

function finalizeTurnOnErrorOrClose() {
    console.log("[DEBUG] finalizeTurnOnErrorOrClose called.");
    if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK && currentCodeBlockElement) {
        console.warn("Stream ended unexpectedly inside code block. Finalizing highlight.");
        try { finalizeCodeBlock(true); } catch (e) { console.error("Prism highlight error on close:", e); }
    }
    if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE ||
        currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY ||
        currentProcessingMode === MODE_MAYBE_START_DISPLAY_KATEX) {
        console.warn("Stream ended unexpectedly inside KaTeX block. Processing raw text.");
        if (currentKatexMarkerId) {
            renderAndReplaceKatex(currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY, currentKatexMarkerId);
            currentKatexMarkerId = null;
        }
    }
     if (currentProcessingMode === MODE_INSIDE_THINK || currentProcessingMode === MODE_MAYBE_END_THINK || currentProcessingMode === MODE_SEEKING_TAG) {
         console.warn("Stream ended unexpectedly inside/seeking Think tags. Finishing think buffer:", thinkBuffer);
         if (currentThinkingArea && currentThinkingArea.style.display !== 'none' && currentThinkingPreElement) {
              appendRawTextToThinkingArea("\n--- (Stream ended unexpectedly during thinking) ---");
         }
     }

    formatAnswerBubbleFinal();
    resetStreamingState();
    setInputDisabledState(true);
}

// --- Code Block Functions ---

function createCodeBlockStructure(language) {
    if (!currentCodeBlocksArea) {
        console.error("Code blocks area is null!");
        return;
    }
    codeBlockCounterThisTurn++;
    const currentCodeNumber = codeBlockCounterThisTurn;
    const blockId = `code-block-turn${currentTurnId}-${currentCodeNumber}`;
    const safeLanguage = (language || '').trim().toLowerCase() || 'plain';

    const langAlias = {
        'python': 'python', 'py': 'python',
        'javascript': 'javascript', 'js': 'javascript',
        'html': 'markup', 'xml': 'markup', 'svg': 'markup',
        'css': 'css',
        'bash': 'bash', 'sh': 'bash', 'shell': 'bash',
        'json': 'json',
        'yaml': 'yaml', 'yml': 'yaml',
        'markdown': 'markdown', 'md': 'markdown',
        'sql': 'sql',
        'java': 'java',
        'c': 'c',
        'cpp': 'cpp', 'c++': 'cpp',
        'csharp': 'csharp', 'cs': 'csharp',
        'go': 'go',
        'rust': 'rust',
        'php': 'php',
        'ruby': 'ruby', 'rb': 'ruby',
        'dockerfile': 'docker', 'docker': 'docker',
        'typescript': 'typescript', 'ts': 'typescript',
        'plaintext': 'plain', 'text': 'plain',
       };
    const prismLang = langAlias[safeLanguage] || safeLanguage;
    const displayLang = safeLanguage;
    currentCodeBlockLang = prismLang; // Store the actual Prism language used

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    const container = document.createElement('div');
    container.classList.add('code-block-container');
    container.id = blockId;
    container.dataset.language = safeLanguage;

    const codeHeader = document.createElement('div');
    codeHeader.classList.add('code-block-header');

    const codeButtonsDiv = document.createElement('div');
    codeButtonsDiv.classList.add('code-block-buttons');

    const runStopBtn = document.createElement('button');
    runStopBtn.classList.add('run-code-btn', 'code-action-btn');
    runStopBtn.dataset.status = 'idle';
    runStopBtn.innerHTML = playIconSvg;
    runStopBtn.title = 'Run Code';

    const toggleCodeBtn = document.createElement('button');
    toggleCodeBtn.classList.add('toggle-code-btn', 'code-action-btn');
    toggleCodeBtn.textContent = 'Hide';
    toggleCodeBtn.title = 'Show/Hide Code';

    const copyCodeBtn = document.createElement('button');
    copyCodeBtn.classList.add('copy-code-btn', 'code-action-btn');
    copyCodeBtn.textContent = 'Copy';
    copyCodeBtn.title = 'Copy Code';

    codeButtonsDiv.appendChild(runStopBtn);
    codeButtonsDiv.appendChild(toggleCodeBtn);
    codeButtonsDiv.appendChild(copyCodeBtn);

    const codeTitle = document.createElement('span');
    codeTitle.classList.add('code-block-title');
    codeTitle.textContent = `Code ${currentCodeNumber} (${displayLang})`;
    codeTitle.style.flexGrow = '1';
    codeTitle.style.textAlign = 'left';

    codeHeader.appendChild(codeButtonsDiv);
    codeHeader.appendChild(codeTitle);

    const preElement = document.createElement('pre');
    preElement.classList.add('manual'); // Keep manual class if needed for other styles
    const codeElement = document.createElement('code');
    // IMPORTANT: Set the class required by PrismJS
    codeElement.className = `language-${prismLang}`;
    codeElement.setAttribute('contenteditable', 'true');
    codeElement.setAttribute('spellcheck', 'false');

    currentCodeBlockPreElement = preElement;
    currentCodeBlockElement = codeElement;

    const outputHeader = document.createElement('div');
    outputHeader.classList.add('code-output-header');
    outputHeader.style.display = 'none';

    const outputButtonsDiv = document.createElement('div');
    outputButtonsDiv.classList.add('code-block-buttons');

    const placeholderSpan = document.createElement('span');
    placeholderSpan.classList.add('output-header-button-placeholder');
    outputButtonsDiv.appendChild(placeholderSpan);

    const toggleOutputBtn = document.createElement('button');
    toggleOutputBtn.classList.add('toggle-output-btn', 'code-action-btn');
    toggleOutputBtn.textContent = 'Hide';
    toggleOutputBtn.title = 'Show/Hide Output';

    const copyOutputBtn = document.createElement('button');
    copyOutputBtn.classList.add('copy-output-btn', 'code-action-btn');
    copyOutputBtn.textContent = 'Copy';
    copyOutputBtn.title = 'Copy Output';

    outputButtonsDiv.appendChild(toggleOutputBtn);
    outputButtonsDiv.appendChild(copyOutputBtn);

    const outputTitle = document.createElement('span');
    outputTitle.classList.add('output-header-title');
    outputTitle.textContent = `Output Code ${currentCodeNumber}`;

    const codeStatusSpan = document.createElement('span');
    codeStatusSpan.classList.add('code-status-span');
    codeStatusSpan.textContent = 'Idle';

    outputHeader.appendChild(outputButtonsDiv);
    outputHeader.appendChild(outputTitle);
    outputHeader.appendChild(codeStatusSpan);

    const outputConsoleDiv = document.createElement('div');
    outputConsoleDiv.classList.add('code-output-console');
    outputConsoleDiv.style.display = 'none';
    const outputPre = document.createElement('pre');
    outputConsoleDiv.appendChild(outputPre);

    preElement.appendChild(codeElement);
    container.appendChild(codeHeader);
    container.appendChild(preElement);
    container.appendChild(outputHeader);
    container.appendChild(outputConsoleDiv);

    toggleCodeBtn.addEventListener('click', () => {
        const isHidden = preElement.classList.toggle('hidden');
        toggleCodeBtn.textContent = isHidden ? 'Show' : 'Hide';
    });

    copyCodeBtn.addEventListener('click', async () => {
         if (!codeElement) return;
        try {
            await navigator.clipboard.writeText(codeElement.textContent || '');
            copyCodeBtn.textContent = 'Copied!';
            copyCodeBtn.classList.add('copied');
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; copyCodeBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy code: ', err);
            copyCodeBtn.textContent = 'Error';
            setTimeout(() => { copyCodeBtn.textContent = 'Copy'; }, 1500);
        }
    });

    runStopBtn.addEventListener('click', handleRunStopCodeClick);

    const debouncedHighlight = debounce(() => {
         // console.log(`Highlighting ${blockId} after edit.`);
        const savedPosition = getCursorPosition(codeElement);
        if (savedPosition === -1) { console.warn("Could not save cursor position or cursor not in element. Highlight may cause cursor jump."); }
        try {
            // Remove existing spans before re-highlighting
            // This is simpler than trying to replace nodes in place
            const currentText = codeElement.textContent;
            codeElement.innerHTML = ''; // Clear spans
            codeElement.textContent = currentText; // Restore text

            Prism.highlightElement(codeElement);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        } catch (e) {
            console.error("Error during debounced highlighting:", e);
            if (savedPosition !== -1) { setCursorPosition(codeElement, savedPosition); }
        }
    }, 500);
    codeElement.addEventListener('input', debouncedHighlight);
    codeElement.addEventListener('paste', (e) => { setTimeout(debouncedHighlight, 100); });

    toggleOutputBtn.addEventListener('click', () => {
        const isHidden = outputConsoleDiv.classList.toggle('hidden');
        toggleOutputBtn.textContent = isHidden ? 'Show' : 'Hide';
    });

    copyOutputBtn.addEventListener('click', async () => {
        if (!outputPre) return;
        try {
            await navigator.clipboard.writeText(outputPre.textContent || '');
            copyOutputBtn.textContent = 'Copied!';
            copyOutputBtn.classList.add('copied');
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; copyOutputBtn.classList.remove('copied'); }, 1500);
        } catch (err) {
            console.error('Failed to copy output: ', err);
            copyOutputBtn.textContent = 'Error';
            setTimeout(() => { copyOutputBtn.textContent = 'Copy'; }, 1500);
        }
    });

    currentCodeBlocksArea.appendChild(container);
    lastAppendedNode = null;
}

async function handleRunStopCodeClick(event) {
    const button = event.currentTarget;
    const container = button.closest('.code-block-container');
    if (!container) return;

    const codeBlockId = container.id;
    const language = container.dataset.language;
    const codeElement = container.querySelector('code');
    const outputHeader = container.querySelector('.code-output-header');
    const outputConsoleDiv = container.querySelector('.code-output-console');
    const outputPre = outputConsoleDiv ? outputConsoleDiv.querySelector('pre') : null;
    const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

    if (!language || !codeElement || !outputHeader || !outputConsoleDiv || !outputPre || !statusSpan) {
        console.error(`Missing elements for code block ${codeBlockId}`);
        addErrorMessage(`Cannot run code block ${codeBlockId}: Internal UI error.`);
        return;
    }

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;
    const stopIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
    const stoppingIconSvg = `<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style="display: block;"><path d="M12 2V6M12 18V22M6 12H2M22 12H18M19.0711 4.92893L16.2426 7.75736M7.75736 16.2426L4.92893 19.0711M19.0711 19.0711L16.2426 16.2426M7.75736 7.75736L4.92893 4.92893" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const code = codeElement.textContent || '';
    const currentStatus = button.dataset.status;

    if (!websocket || websocket.readyState !== WebSocket.OPEN) {
        console.warn("WebSocket not open. Cannot run/stop code.");
        statusSpan.textContent = 'Error: Disconnected';
        statusSpan.className = 'code-status-span error';
        outputHeader.style.display = 'flex';
        addErrorMessage("Cannot run/stop code: Not connected to server.");
        return;
    }

    if (currentStatus === 'idle') {
        console.log(`Requesting run for block ${codeBlockId} (${language})`);
        button.dataset.status = 'running';
        button.innerHTML = stopIconSvg;
        button.title = 'Stop Execution';

        outputPre.innerHTML = '';
        outputHeader.style.display = 'flex';
        outputConsoleDiv.style.display = 'block';
        outputConsoleDiv.classList.remove('hidden');
        const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
        if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';

        statusSpan.textContent = 'Running...';
        statusSpan.className = 'code-status-span running';

        websocket.send(JSON.stringify({
            type: 'run_code',
            payload: { code_block_id: codeBlockId, language: language, code: code }
        }));

    } else if (currentStatus === 'running') {
        console.log(`Requesting stop for block ${codeBlockId}`);
        button.dataset.status = 'stopping';
        button.innerHTML = stoppingIconSvg;
        button.title = 'Stopping...';
        button.disabled = true;

        statusSpan.textContent = 'Stopping...';
        statusSpan.className = 'code-status-span stopping';

        websocket.send(JSON.stringify({
            type: 'stop_code',
            payload: { code_block_id: codeBlockId }
        }));
    } else if (currentStatus === 'stopping') {
        console.log(`Already stopping block ${codeBlockId}`);
    }
}

function addCodeOutput(outputPreElement, streamType, text) {
    if (!outputPreElement || !text) return;

    const span = document.createElement('span');
    span.classList.add(streamType === 'stderr' ? 'stderr-output' : 'stdout-output');
    span.textContent = text;
    outputPreElement.appendChild(span);
    outputPreElement.scrollTop = outputPreElement.scrollHeight;
}

function appendToCodeBlock(text) {
    if (currentCodeBlockElement) {
        currentCodeBlockElement.appendChild(document.createTextNode(text));
        if(currentCodeBlockPreElement && !currentCodeBlockPreElement.classList.contains('hidden')) {
            const isNearCodeBottom = currentCodeBlockPreElement.scrollHeight - currentCodeBlockPreElement.scrollTop - currentCodeBlockPreElement.clientHeight < 50;
            if(isNearCodeBottom) {
                requestAnimationFrame(() => { currentCodeBlockPreElement.scrollTop = currentCodeBlockPreElement.scrollHeight; });
            }
        }
    } else {
        console.error("Attempted to append to null code block element!");
    }
}

function appendToAnswer(text = null, node = null) {
    if (!currentAnswerElement) return;

    let isMeaningfulContent = (text && text.trim().length > 0) ||
                                (node && node.nodeType !== Node.COMMENT_NODE && (!node.textContent || node.textContent.trim().length > 0));

    if (!firstAnswerTokenReceived && isMeaningfulContent) {
        // console.log("[appendToAnswer] First meaningful answer content received.");
        if (currentAnswerElement.style.display === 'none') {
            // console.log("[appendToAnswer] Making answer element visible.");
            currentAnswerElement.style.display = '';
        }
        const loadingDots = currentAnswerElement.querySelector('.loading-dots');
        if (loadingDots) {
            // console.log("[appendToAnswer] Removing loading dots.");
            loadingDots.remove();
        }
        firstAnswerTokenReceived = true;
    }

    if (node) {
        if (!node.classList || !node.classList.contains('loading-dots')) {
             currentAnswerElement.appendChild(node);
             lastAppendedNode = node;
        }
    } else if (text !== null && text.length > 0) {
        accumulatedAnswerText += text;
        if (lastAppendedNode && lastAppendedNode.nodeType === Node.TEXT_NODE && lastAppendedNode.parentNode === currentAnswerElement) {
            lastAppendedNode.nodeValue += text;
        } else {
            const textNode = document.createTextNode(text);
            currentAnswerElement.appendChild(textNode);
            lastAppendedNode = textNode;
        }
    }
}

function finalizeCodeBlock(isTruncated = false) {
    if (currentCodeBlockElement) {
        const blockId = currentCodeBlockElement.closest('.code-block-container')?.id;
        const langClass = currentCodeBlockElement.className; // Get the class name

        // --- Add these detailed logs ---
        console.log(`[finalizeCodeBlock] Attempting highlight on element:`, currentCodeBlockElement);
        console.log(`[finalizeCodeBlock] Element class: ${langClass}`);
        console.log(`[finalizeCodeBlock] Prism.languages.python just before highlight:`, Prism.languages.python);
        // -------------------------------

        try {
            // Ensure normalization before final highlight
            currentCodeBlockElement.normalize();
            // Explicitly call highlightElement
            Prism.highlightElement(currentCodeBlockElement);
            console.log(`[finalizeCodeBlock] Highlighting finished successfully for ${blockId}.`); // Log success
        } catch (e) {
            // Log errors specifically from Prism.highlightElement
            console.error(`Prism highlight error on finalize (lang '${currentCodeBlockLang}'):`, e);
        }
    } else {
         console.warn("[finalizeCodeBlock] Called but currentCodeBlockElement is null.");
    }
    // Reset tracking variables AFTER the block logic
    currentCodeBlockElement = null;
    currentCodeBlockPreElement = null;
    currentCodeBlockLang = '';
}


function resetAllCodeButtonsOnErrorOrClose() {
    console.log("Resetting all code run/stop buttons and statuses due to connection issue.");
    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    document.querySelectorAll('.code-block-container').forEach(container => {
        const button = container.querySelector('.run-code-btn');
        const outputHeader = container.querySelector('.code-output-header');
        const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

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

// --- WebSocket Handling ---

function connectWebSocket() {

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/${clientId}`;
    console.log(`[DEBUG] Attempting to connect to WebSocket: ${wsUrl}`);

    const playIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><polygon points="0,0 100,50 0,100"/></svg>`;

    try {
        const ws = new WebSocket(wsUrl);
        console.log('connectWebSocket: Creating WebSocket object...');

        ws.onopen = (event) => {
            console.log("[DEBUG] WebSocket connection opened. ws object:", ws);
            websocket = ws;

            if (typeof addSystemMessage !== 'function' || typeof setupNewAiTurn !== 'function' || typeof appendToAnswer !== 'function' || typeof formatAnswerBubbleFinal !== 'function' || typeof resetStreamingState !== 'function' || typeof setInputDisabledState !== 'function') {
                console.error("CRITICAL ERROR: One or more required helper functions are not defined when ws.onopen is called. Check script order.");
                alert("Chat initialization failed. Please refresh the page. (Error: Helpers undefined)");
                setInputDisabledState(true);
                return;
            }

            setInputDisabledState(false);
            addSystemMessage("Connected to the chat server.");

            console.log("[DEBUG ws.onopen] Setting up initial AI turn for welcome message.");
            thinkingRequestedForCurrentTurn = false;
            setupNewAiTurn();
            const welcomeMessage = "Hello! How can I help you today?";
            appendToAnswer(welcomeMessage);
            formatAnswerBubbleFinal();
            console.log("[DEBUG ws.onopen] Resetting state after welcome message.");
            resetStreamingState();

            if(messageInput && messageInput.offsetParent !== null) messageInput.focus();
        };

        ws.onmessage = (event) => {
            let isJsonMessage = false;
            let messageData = null;
            try {
                if (typeof event.data === 'string' && event.data.startsWith('{')) {
                    messageData = JSON.parse(event.data);
                    if (messageData && messageData.type && messageData.payload && messageData.payload.code_block_id) {
                        isJsonMessage = true;
                    }
                }
            } catch (e) {
                isJsonMessage = false;
            }

            if (isJsonMessage) {
                // console.log("%c[ws.onmessage] Code Execution JSON received:", "color: blue;", messageData);
                const { type, payload } = messageData;
                const { code_block_id } = payload;
                const container = document.getElementById(code_block_id);

                if (!container) {
                    console.warn(`Received message for unknown code block ID: ${code_block_id}`, payload);
                    return;
                }

                const outputHeader = container.querySelector('.code-output-header');
                const outputConsoleDiv = container.querySelector('.code-output-console');
                const outputPre = outputConsoleDiv ? outputConsoleDiv.querySelector('pre') : null;
                const runStopBtn = container.querySelector('.run-code-btn');
                const statusSpan = outputHeader ? outputHeader.querySelector('.code-status-span') : null;

                if (!outputHeader || !outputPre || !runStopBtn || !statusSpan) {
                     console.error(`Missing elements for code block ${code_block_id}. Cannot process message.`);
                     return;
                }

                switch (type) {
                    case 'code_output':
                        const { stream, data } = payload;
                        if (outputHeader.style.display === 'none') {
                            // console.log(`Showing output header for ${code_block_id} on first output.`);
                            outputHeader.style.display = 'flex';
                        }
                        if (outputConsoleDiv.style.display === 'none') {
                            // console.log(`Showing output console for ${code_block_id} on first output.`);
                            outputConsoleDiv.style.display = 'block';
                            outputConsoleDiv.classList.remove('hidden');
                             const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
                             if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';
                        }
                        if (runStopBtn.dataset.status === 'idle'){
                             const stopIconSvg = `<svg viewBox="0 0 100 100" fill="currentColor" width="1em" height="1em" style="display: block;"><rect width="100" height="100" rx="15"/></svg>`;
                             console.warn(`Received code output for ${code_block_id} while button was idle. Forcing state to running.`);
                             runStopBtn.dataset.status = 'running';
                             runStopBtn.innerHTML = stopIconSvg;
                             runStopBtn.title = 'Stop Execution';
                             runStopBtn.disabled = false;
                             statusSpan.textContent = 'Running...';
                             statusSpan.className = 'code-status-span running';
                        }
                        addCodeOutput(outputPre, stream, data);
                        break;

                    case 'code_finished':
                        if (outputHeader.style.display === 'none') {
                            // console.log(`Showing output header for ${code_block_id} on finish.`);
                            outputHeader.style.display = 'flex';
                        }
                         if (outputConsoleDiv.style.display === 'none') {
                            // console.log(`Showing output console for ${code_block_id} on finish.`);
                            outputConsoleDiv.style.display = 'block';
                            outputConsoleDiv.classList.remove('hidden');
                              const toggleOutputBtn = outputHeader.querySelector('.toggle-output-btn');
                             if (toggleOutputBtn) toggleOutputBtn.textContent = 'Hide';
                        }

                        const { exit_code, error } = payload;
                        let finishMessage = '';
                        let statusClass = '';

                        if (error) {
                            if (error === "Execution stopped by user.") {
                                finishMessage = 'Stopped'; statusClass = 'stopped';
                            } else if (error.startsWith("Execution timed out")) {
                                 finishMessage = 'Timeout'; statusClass = 'error';
                            } else {
                                 finishMessage = `Error (${exit_code !== undefined ? exit_code : 'N/A'})`; statusClass = 'error';
                                 console.error(`Execution error for ${code_block_id}:`, error);
                            }
                        } else {
                            finishMessage = `Finished (Exit: ${exit_code})`;
                            statusClass = exit_code === 0 ? 'success' : 'error';
                        }

                        statusSpan.textContent = finishMessage;
                        statusSpan.className = `code-status-span ${statusClass}`;

                        runStopBtn.dataset.status = 'idle';
                        runStopBtn.innerHTML = playIconSvg;
                        runStopBtn.title = 'Run Code';
                        runStopBtn.disabled = false;
                        break;

                    default:
                        console.warn(`Received unknown code execution message type: ${type}`, payload);
                }

            } else {
                // console.log("%c[ws.onmessage] RAW chat data received:", "color: magenta;", event.data);
                let chunk = event.data;
                const currentTurnIdForMsg = currentTurnId;

                if (chunk === "<EOS>") {
                    console.log(`%c[ws.onmessage] Turn ${currentTurnIdForMsg}: Received <EOS>. Finalizing turn.`, 'color: green; font-weight: bold;');
                    if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK) {
                         console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received inside code block. Appending fence buffer and finalizing.`);
                         if (fenceBuffer.length > 0) appendToCodeBlock(fenceBuffer);
                         try { finalizeCodeBlock(true); } catch (e) { console.error("Error finalizing code block on EOS:", e); }
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_END_FENCE) {
                         console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received while seeking end fence. Treating '${fenceBuffer}' as code.`);
                         appendToCodeBlock(fenceBuffer);
                         finalizeCodeBlock(true);
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_LANGUAGE && langBuffer.length > 0) {
                         console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received while seeking language. Treating '${FENCE + langBuffer}' as text.`);
                         appendToAnswer(FENCE + langBuffer);
                    } else if (currentProcessingMode === MODE_SEEKING_CODE_START_FENCE && fenceBuffer.length > 0) {
                         console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received while seeking start fence. Treating '${fenceBuffer}' as text.`);
                         appendToAnswer(fenceBuffer);
                    } else if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE || currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY) {
                         console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received inside KaTeX buffer. Attempting render.`);
                         if (currentKatexMarkerId) { renderAndReplaceKatex(currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY, currentKatexMarkerId); currentKatexMarkerId = null; }
                    } else if (currentProcessingMode === MODE_INSIDE_THINK || currentProcessingMode === MODE_MAYBE_END_THINK || currentProcessingMode === MODE_SEEKING_TAG) {
                          console.warn(`Turn ${currentTurnIdForMsg}: <EOS> received unexpectedly inside/seeking Think tags. Last mode: ${currentProcessingMode}`);
                          if (thinkingRequestedForCurrentTurn && currentThinkingArea && currentThinkingPreElement) { appendRawTextToThinkingArea("\n--- (EOS received mid-think) ---"); }
                    }

                    formatAnswerBubbleFinal();
                    resetStreamingState();
                    setInputDisabledState(false);
                    if (messageInput && messageInput.offsetParent !== null) { messageInput.focus(); }
                    setTimeout(() => scrollToBottom('smooth'), 50);
                    return;
                }
                if (chunk.startsWith("<ERROR>")) {
                    const errorMessage = chunk.substring(7);
                    console.error(`[ws.onmessage] Turn ${currentTurnIdForMsg}: Received <ERROR>:`, errorMessage);
                    addErrorMessage(errorMessage);
                    finalizeTurnOnErrorOrClose();
                    resetAllCodeButtonsOnErrorOrClose();
                    setTimeout(() => scrollToBottom('smooth'), 50);
                    return;
                }

                if (chunk.length === 0) {
                     return;
                }

                if (!currentAiTurnContainer) {
                    if (chunk.trim().length > 0) {
                        console.warn(`%c[ws.onmessage] Turn ${currentTurnIdForMsg}: First non-empty chat chunk received, but turn container not set up? Forcing setup.`, 'color: red; font-weight: bold;');
                        setupNewAiTurn();
                    } else {
                        return;
                    }
                }

                let currentPos = 0;
                while (currentPos < chunk.length) {
                    const char = chunk[currentPos];
                    let incrementPos = true;
                    let previousMode = currentProcessingMode;

                    if (char === '\\' && currentPos + 1 < chunk.length) {
                        const nextChar = chunk[currentPos + 1];
                        const escapableChars = '$`*\\<>';
                        if (escapableChars.includes(nextChar)) {
                            if (currentProcessingMode === MODE_INSIDE_THINK) { appendRawTextToThinkingArea(nextChar); }
                            else if (currentProcessingMode === MODE_KATEX_BUFFERING_INLINE || currentProcessingMode === MODE_KATEX_BUFFERING_DISPLAY) { katexBuffer += nextChar; appendToAnswer(nextChar); }
                            else if (currentProcessingMode === MODE_INSIDE_CODE_BLOCK) { appendToCodeBlock(nextChar); }
                            else { appendToAnswer(nextChar); }
                            currentPos += 2;
                            incrementPos = false;
                            continue;
                        }
                    }

                    switch (currentProcessingMode) {

                        case MODE_ANSWER:
                            if (char === FENCE[0]) {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                katexBuffer = ''; currentKatexMarkerId = null;
                                fenceBuffer = char;
                                currentProcessingMode = MODE_SEEKING_CODE_START_FENCE;
                            } else if (char === '$') {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                currentProcessingMode = MODE_MAYBE_START_DISPLAY_KATEX;
                            } else if (char === '<') {
                                 if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                tagBuffer = char;
                                currentProcessingMode = MODE_SEEKING_TAG;
                            } else {
                                if (previousMode === MODE_MAYBE_START_DISPLAY_KATEX) { appendToAnswer('$'); }
                                appendToAnswer(char);
                            }
                            break;

                        case MODE_SEEKING_CODE_START_FENCE:
                            if (char === FENCE[fenceBuffer.length]) {
                                fenceBuffer += char;
                                if (fenceBuffer === FENCE) {
                                    fenceBuffer = ''; langBuffer = '';
                                    currentProcessingMode = MODE_SEEKING_CODE_LANGUAGE;
                                }
                            } else {
                                appendToAnswer(fenceBuffer);
                                fenceBuffer = '';
                                currentProcessingMode = MODE_ANSWER;
                                incrementPos = false;
                            }
                            break;

                        case MODE_SEEKING_CODE_LANGUAGE:
                             if (char === '\n') {
                                 currentCodeBlockLang = langBuffer.trim(); // Language name stored here
                                 createCodeBlockStructure(currentCodeBlockLang); // Pass language name
                                 appendCodeReference();
                                 langBuffer = '';
                                 currentProcessingMode = MODE_INSIDE_CODE_BLOCK;
                             } else if (langBuffer.length > 50) {
                                 console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: Code language line too long. Treating as text.`);
                                 appendToAnswer(FENCE + langBuffer + char);
                                 langBuffer = ''; fenceBuffer = '';
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
                                    finalizeCodeBlock(); // Call finalize here
                                    fenceBuffer = '';
                                    currentProcessingMode = MODE_ANSWER;
                                    lastAppendedNode = null;
                                    if (currentPos + 1 < chunk.length && chunk[currentPos + 1] === '\n') {
                                        currentPos++;
                                        incrementPos = true; // Ensure newline is skipped
                                    }
                                }
                            } else {
                                appendToCodeBlock(fenceBuffer); // Append incomplete fence as code
                                fenceBuffer = '';
                                currentProcessingMode = MODE_INSIDE_CODE_BLOCK; // Go back to code mode
                                incrementPos = false; // Re-process current char in code mode
                            }
                            break;

                        case MODE_MAYBE_START_DISPLAY_KATEX:
                            if (char === '$') {
                                currentKatexMarkerId = `katex-marker-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
                                const startMarkerDisp = document.createElement('span');
                                startMarkerDisp.setAttribute('data-katex-start-id', currentKatexMarkerId);
                                startMarkerDisp.style.display = 'none';
                                appendToAnswer(null, startMarkerDisp);
                                appendToAnswer('$$');
                                currentProcessingMode = MODE_KATEX_BUFFERING_DISPLAY;
                                katexBuffer = '';
                            } else {
                                currentKatexMarkerId = `katex-marker-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
                                const startMarkerInline = document.createElement('span');
                                startMarkerInline.setAttribute('data-katex-start-id', currentKatexMarkerId);
                                startMarkerInline.style.display = 'none';
                                appendToAnswer(null, startMarkerInline);
                                appendToAnswer('$');
                                appendToAnswer(char);
                                currentProcessingMode = MODE_KATEX_BUFFERING_INLINE;
                                katexBuffer = char;
                            }
                            break;

                        case MODE_KATEX_BUFFERING_INLINE:
                            if (char === '$') {
                                appendToAnswer('$');
                                if (currentKatexMarkerId) { renderAndReplaceKatex(false, currentKatexMarkerId); currentKatexMarkerId = null; }
                                else { console.error(`Attempted end inline KaTeX Turn ${currentTurnIdForMsg} no marker ID!`); }
                                currentProcessingMode = MODE_ANSWER;
                            } else { katexBuffer += char; appendToAnswer(char); }
                            break;

                        case MODE_KATEX_BUFFERING_DISPLAY:
                             if (char === '$' && currentPos + 1 < chunk.length && chunk[currentPos + 1] === '$') {
                                 appendToAnswer('$$');
                                 if (currentKatexMarkerId) { renderAndReplaceKatex(true, currentKatexMarkerId); currentKatexMarkerId = null; }
                                 else { console.error(`Attempted end display KaTeX Turn ${currentTurnIdForMsg} no marker ID!`); }
                                 currentProcessingMode = MODE_ANSWER; currentPos++;
                             } else { katexBuffer += char; appendToAnswer(char); }
                             break;

                        case MODE_SEEKING_TAG:
                            tagBuffer += char;
                            const lowerTag = tagBuffer.toLowerCase();
                            if (lowerTag === THINK_START_TAG) {
                                thinkBuffer = ''; currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; lastAppendedNode = null;
                            } else if (THINK_START_TAG.startsWith(lowerTag)) { /* Keep buffering */ }
                            else {
                                appendToAnswer(tagBuffer); currentProcessingMode = MODE_ANSWER; tagBuffer = ''; incrementPos = false;
                            }
                            if (tagBuffer.length > 20) {
                                 console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: Tag buffer excessive, treating as text: ${tagBuffer}`);
                                 appendToAnswer(tagBuffer); currentProcessingMode = MODE_ANSWER; tagBuffer = ''; incrementPos = false;
                            }
                            break;

                         case MODE_INSIDE_THINK:
                              if (char === '<') {
                                  tagBuffer = char; currentProcessingMode = MODE_MAYBE_END_THINK;
                              } else { appendRawTextToThinkingArea(char); }
                              break;

                         case MODE_MAYBE_END_THINK:
                              tagBuffer += char;
                              const lowerEndTag = tagBuffer.toLowerCase();
                              if (lowerEndTag === THINK_END_TAG) {
                                  thinkBuffer = ''; tagBuffer = ''; currentProcessingMode = MODE_ANSWER; lastAppendedNode = null;
                              } else if (THINK_END_TAG.startsWith(lowerEndTag)) { /* Keep buffering */ }
                              else {
                                  appendRawTextToThinkingArea(tagBuffer);
                                  currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; incrementPos = false;
                              }
                              if (tagBuffer.length > 20) {
                                   console.warn(`[State Machine] Turn ${currentTurnIdForMsg}: End tag buffer excessive, treating as think text: ${tagBuffer}`);
                                   appendRawTextToThinkingArea(tagBuffer); currentProcessingMode = MODE_INSIDE_THINK; tagBuffer = ''; incrementPos = false;
                              }
                              break;

                        default:
                            console.error(`Unknown processing mode: ${currentProcessingMode} in Turn ${currentTurnIdForMsg}`);
                            appendToAnswer(char);
                            currentProcessingMode = MODE_ANSWER;
                    }

                    if (incrementPos) { currentPos++; }
                }
                scrollToBottom();
            }
        };

        ws.onerror = (event) => {
            console.error("WebSocket error observed:", event);
            addErrorMessage("WebSocket connection error. Please check the server or try refreshing. See console for details.");
            finalizeTurnOnErrorOrClose();
            resetAllCodeButtonsOnErrorOrClose();
            setInputDisabledState(true);
        };

        ws.onclose = (event) => {
            console.log("WebSocket connection closed.", event);
            addSystemMessage(`Connection closed: ${event.reason || 'Normal closure'} (Code: ${event.code})`);
            finalizeTurnOnErrorOrClose();
            resetAllCodeButtonsOnErrorOrClose();
            if (event.code !== 1000 && event.code !== 1005) {
                console.log("Unexpected WebSocket closure. Attempting to reconnect WebSocket in 3 seconds...");
                addSystemMessage("Attempting to reconnect...");
                setInputDisabledState(true);
                setTimeout(() => { websocket = null; resetStreamingState(); currentAiTurnContainer = null; connectWebSocket(); }, 3000);
            } else { setInputDisabledState(true); }
        };

        console.log("[DEBUG] WebSocket object created and handlers assigned.");

     } catch (error) {
         console.error("[DEBUG] CRITICAL Error creating WebSocket object:", error);
         if (typeof addErrorMessage === 'function') { addErrorMessage(`WebSocket Creation Error: ${error.message}. Please check server and refresh.`); }
         else { alert(`WebSocket Creation Error: ${error.message}. Please check server and refresh.`); }
         setInputDisabledState(true);
     }
}

// --- Event Listeners & Initialization ---

if (chatForm) {
    chatForm.addEventListener('submit', (event) => {
        event.preventDefault();
        const userMessage = messageInput.value.trim();
        if (!userMessage) { return; }

        if (!websocket || websocket.readyState !== WebSocket.OPEN) {
            console.warn("[Submit] WebSocket not ready or not defined. State:", websocket?.readyState);
            addErrorMessage("Not connected to the server. Please wait or refresh.");
            return;
        }

        try {
            addUserMessage(userMessage);
            thinkingRequestedForCurrentTurn = thinkCheckbox ? thinkCheckbox.checked : false;
            // console.log(`[Submit] Checkbox checked: ${thinkingRequestedForCurrentTurn}.`);

            let messageToSend = userMessage;
            if (!thinkingRequestedForCurrentTurn) {
                messageToSend = NO_THINK_PREFIX + userMessage;
                // console.log(`[Submit] Prepended '${NO_THINK_PREFIX}'. Prepared message: "${messageToSend.substring(0, 100)}..."`);
            } else {
                // console.log(`[Submit] Prepared raw message (thinking requested): "${messageToSend.substring(0, 100)}..."`);
            }

            setupNewAiTurn();
            // console.log(`[Submit] Sending final message to WebSocket: "${messageToSend.substring(0, 100)}..."`);
            websocket.send(messageToSend);
            // console.log("[Submit] websocket.send() call completed.");

            messageInput.value = '';
            setInputDisabledState(true);
            // console.log("[Submit] Input cleared and disabled.");

        } catch (sendError) {
            console.error("[Submit] !!! ERROR during send attempt or post-send steps:", sendError);
            addErrorMessage(`Failed to send message: ${sendError.message}`);
        }
    });
} else {
    console.error("CRITICAL ERROR: Could not find chat form element with ID 'chat-form'. Messages cannot be sent.");
    if (typeof addErrorMessage === 'function') {
        addErrorMessage("Initialization Error: Chat input form not found.");
    } else {
        alert("Initialization Error: Chat input form not found.");
    }
}

// --- Initial Setup ---
setInputDisabledState(true); // Disable input until WebSocket connects
marked.setOptions({
  gfm: true, breaks: true, sanitize: false, smartLists: true, smartypants: false,
});
connectWebSocket(); // Start WebSocket connection
