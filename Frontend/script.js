const PR_URL_REGEX = /^https?:\/\/(?:www\.)?github\.com\/(.+?)\/(.+?)\/pull\/(\d+?)$/;
const backendPort = 5000;
const backendURL = `${window.location.protocol}//${window.location.hostname}:5000`;
const prParamName = 'url';

// Escape characters reserved in HTML
const encodeEntities = (value) => value
    .replace(/&/g, '&amp;')
    .replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g,
             (value) => `&#${(((value.charCodeAt(0) - 0xD800) * 0x400) + (value.charCodeAt(1) - 0xDC00) + 0x10000)};`)
    .replace(/([^\#-~| |!])/g, (value) => `&#${value.charCodeAt(0)};`)
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

// Get parameter from query string by its name
const getParameterByName = (name) => {
    const url = window.location.href;
    name = name.replace(/[\[\]]/g, '\\$&');
    const regex = new RegExp(`[?&]${name}(=([^&#]*)|&|#|$)`);
    const results = regex.exec(url);
    if (!results) return null;
    if (!results[2]) return '';
    return decodeURIComponent(results[2].replace(/\+/g, ' '));
}

// When the document has finished loading
$(document).ready(() => {
    // Check if a PR URL has been provided
    const pr_url = getParameterByName(prParamName);
    if (pr_url) {
        // If so, populate the URL input field and load the corresponding PR visualization
        $('#pr-url-input').val(pr_url);
        loadVisualization(pr_url);
    }
});

// When the user clicks the "load" button to load a pull request
$('#load-pr').submit((e) => {
    // Prevent actual form submission
    e.preventDefault();
    // Get pull request URL from input field
    const input_url = $('#pr-url-input').val();
    // Change the URL to match the new PR
    history.pushState({}, null, `?${prParamName}=${input_url}`);
    loadVisualization(input_url);
});

// Load the main visualization
const loadVisualization = (pullreq_url) => {
    // Extract user/org, repository name and pull request number from pull request url
    const [, user, repo, pr_number] = PR_URL_REGEX.exec(pullreq_url);

    // Get the diff of the pull request by querying the Github API
    const diff_url = `https://api.github.com/repos/${user}/${repo}/pulls/${pr_number}`;
    $.ajax({
        url: diff_url,
        headers: {'Accept': 'application/vnd.github.v3.diff'}
    })
    .done((data) => {
        // When done, display it
        displayMainDiff(data);
        // Then, load the method calls and display them
        loadMethodCalls(pullreq_url);
    })
    .fail(() => {
        swal({
            text: 'Error retrieving pull request',
            icon: 'error'
        });
    });
}

// Display the main diff, without the method calls
const displayMainDiff = (data) => {
    // Draw the diff in the .diff-container element and highlight the code
    const diff2htmlUi = new Diff2HtmlUI({diff: data});
    diff2htmlUi.draw('.diff-container');
    diff2htmlUi.highlightCode('.diff-container');

    // Add left and right columns (sidebars) for callers and callees
    $('.d2h-diff-tbody tr').prepend('<td class="callsx"></td>');
    $('.d2h-diff-tbody tr').append('<td class="calldx"></td>');

    // Add "loading" text to sidebars while the method calls are being extracted
    const loadingSpinner = '<div class="lds-ellipsis"><div></div><div></div><div></div><div></div></div>';
    const firstSidebarsCells = $('tr:first-child > td.callsx, tr:first-child > td.calldx');
    firstSidebarsCells.addClass('loading');
    firstSidebarsCells.removeClass('header');
    firstSidebarsCells.html(loadingSpinner);

    // Fix for chrome bug that skips the whitespaces at the start of a <span>
    // with "white-space: pre-wrap;"" property.
    $('.d2h-code-line-ctn').each(function() {
        let lineContents = $(this).html();
        if (lineContents.startsWith(' ')) {
            let nWhiteSpacesAtStart = 0;
            for (let char of lineContents) {
                if (char === ' ') nWhiteSpacesAtStart += 1;
                else break;
            }
            const whiteSpacesString = ' '.repeat(nWhiteSpacesAtStart);
            $(`<span class="spacing">${whiteSpacesString}</span>`).insertBefore(this);
            $(this).html(lineContents.substr(nWhiteSpacesAtStart));
        }
    });
};

// Request method calls from backend and when they become available, display them
const loadMethodCalls = (pullreq_url) => {
    const startReviewURL = `${backendURL}/review/start?pr=${pullreq_url}`;
    $.getJSON(startReviewURL)
        .done((data) => {
            // Error retrieving method calls
            if (data.status !== 'ok') {
                mcRetrievalError(data.error);
                return;
            }
            // If the review is still processing, retry after 1 second
            if (data.data.review_status === 'processing') setTimeout(() => { loadMethodCalls(pullreq_url); }, 1000);
            // If the review is ready, display the method calls
            if (data.data.review_status === 'ready') displayMethodCalls(data.data.id);
        })
        .fail(() => {
            // If something goes wrong while trying to get the method calls, signal it
            mcRetrievalError('Cannot communicate with method call extraction backend');
        });
}

// Compute map used to associate modified lines to method calls
// The idea is to create an object containing the information on which lines of the old and new version
// of each file are shown in the diff, together with a reference to those lines.
// The map looks like this:
// {'path/to/file1.java': {lineNumbers: [{oldN: 123, newN: 456}, ...], reference: $(el)}, ...}
const computeMap = () => {
    const map = {};
    // Iterate over each of the changed files
    $('.d2h-file-wrapper').each(function() {
        // Get the path of the changed file
        const filePath = $(this).find('.d2h-file-name').text();
        // If it's a rename skip the file
        if (filePath.includes('→')) return;

        // Find the cells containing the old and new line numbers
        const lineNumbers = $(this).find('.d2h-code-linenumber')
            .map(function() {
                // Extract old and new line numbers
                const oldLineNumberDiv = $(this).find('.line-num1');
                const oldLineNumber = oldLineNumberDiv ? parseInt(oldLineNumberDiv.text(), 10) : null;
                const newLineNumberDiv = $(this).find('.line-num2');
                const newLineNumber = newLineNumberDiv ? parseInt(newLineNumberDiv.text(), 10) : null;
                return {oldN: oldLineNumber, newN: newLineNumber};
            })
            .get();

        // Store in the map the relevant line numbers for each file, together with a reference
        // to the element containing the modified file
        map[filePath] = { lineNumbers, reference: $(this) };
    });
    return map;
};

// Error during retrieval of method calls from backend API
const mcRetrievalError = (error) => {
    // Remove 'loading' spinner
    $('tr:first-child > td.callsx, tr:first-child > td.calldx').removeClass('loading');
    $('tr:first-child > td.callsx, tr:first-child > td.calldx').html('');

    swal({
        text: error,
        icon: 'error'
    });
};

// Popup of modal containing either the caller or the callee
const popupClass = (review_id, type, file_path, start_line, end_line) => {
    // Append to the modal HTML element the type and line range info
    $('.modal').data('type', type);
    $('.modal').data('startline', start_line);
    $('.modal').data('endline', end_line);

    // Basing on the type, show only the source code or the diff
    if (type === 'normal') {
        $('.modal').addClass('normal').removeClass('diff');
        // Get file from backend
        const fileSourceURL =  `${backendURL}/review/${review_id}/file?path=${file_path}`;
        $.getJSON(fileSourceURL)
            .done((data) => {
                const modal_contents = `<div class="source-code"><pre><code class="java">${encodeEntities(data.data)}</code></pre></div>`;
                $('.modal-title').text(file_path);
                // Set contents of modal, and show it
                $('.modal-body').html(modal_contents);
                $('.modal-body code').each(function(i, block) {
                    // Apply syntax highlight and show line numbers on the left
                    hljs.highlightBlock(block);
                    hljs.lineNumbersBlock(block);
                });
                $('#modal-source').modal('show');
            });
    } else {
        $('.modal').addClass('diff').removeClass('normal');
        // Get diff from backend
        const diffSourceURL =  `${backendURL}/review/${review_id}/diff?path=${file_path}`;
        $.getJSON(diffSourceURL)
            .done((data) => {
                $('.modal-title').text(`[✎] ${file_path}`);
                const diff2htmlUi = new Diff2HtmlUI({diff: data.data});
                // Draw the diff in the modal and then show it
                diff2htmlUi.draw('.modal-body');
                diff2htmlUi.highlightCode('.modal-body');
                // Mark the relevant lines so that we can style them accordingly
                for (let line_n = start_line; line_n <= end_line; line_n += 1) {
                    $('.modal-body').find('.line-num2')
                        .filter(function() { return parseInt($(this).text(), 10) === line_n; })
                        .each(function() {
                            $(this).parent().parent().addClass('relevant');
                        });
                }
                $('#modal-source').modal('show');
            });
    }
}

// Organize the method calls returned from the backend basing on the displayed portion
// of the files in the diff, making it easier to place the method call information in the right position
const organizeMethodCalls = (allMethodCalls, map) => {
    const organizedMethodCalls = {};

    // Iterate over each of the modified files displayed in the visualization (except renames)
    for (const [modified_filename, { lineNumbers, reference }] of Object.entries(map)) {
        const file_methodcalls = { callees: [], callers: [] };

        // Callees organization.
        // For the callees it's quite easy as there is 1 or more callee for each of the lines of the new version
        // of the files shown in the visualization.
        // First, we extract the shown line numbers of the file corresponding to its new version.
        const new_version_line_numbers = lineNumbers.filter(({ oldN, newN }) => !isNaN(newN));
        // Then, we extract from the array with all the method calls, the ones originating from the file under analysis
        const file_callees = allMethodCalls.filter(({ from_file }) => from_file === modified_filename);
        // We iterate over the line numbers of the new version of the file (we want to aggregate by line number)
        for (const { newN } of new_version_line_numbers) {
            // We extract all the callees in the line under analysis.
            const line_callees = file_callees.filter(({ call_start_line }) => call_start_line === newN);
            // If there was at least one, add them to the organized object
            if (line_callees.length) file_methodcalls.callees.push({ newN, callees: line_callees });
        }

        // Callers organization.
        // It is a bit more complex than the callees because the callers span multiple lines.
        // Meaning that a caller is associated to a line range in the diff.
        // The following is an algorithm that merges different portions of a method declaration that
        // can be broken because of a diff header (@@ .. @@) or a deleted line.
        // Assumption: there are no overlappings in the method declarations.

        // Extract all the callers of the file under analysis
        const file_callers = allMethodCalls.filter(({ to_file }) => to_file === modified_filename);
        // Scan the diff from top to bottom and identify consecutive ranges of callers
        let index = 0, start_index = -1, end_index = -1;
        while (index < lineNumbers.length) {
            let { newN } = lineNumbers[index];
            // Skip header and deleted lines at the start
            if (isNaN(newN)) {
                index += 1;
                continue;
            }
            // Helper method to check if line number 'n' is within the range of method declaration of method call 'mc'
            const isWithinMethodDeclaration = (mc, n) => mc.declaration_start_line <= newN && newN <= mc.declaration_end_line;
            // Extract all the method calls whose declaration contains the line under analysis
            const method_calls = file_callers.filter(method_call => isWithinMethodDeclaration(method_call, newN));
            // If there is at least one method call..
            if (method_calls.length) {
                // Assumption: all the method declarations starting from a certain line also end on the same line.
                // Therefore we take as reference the method declaration corresponding to the first method call.
                const mc = method_calls[0];
                // We started a method declaration range.
                start_index = index;
                end_index = index;
                // Continue scanning to identify the end of the range.
                while (index < lineNumbers.length - 1) {
                    index += 1;
                    newN = lineNumbers[index].newN;
                    // If newN is NaN, it's either a deleted line or a diff header.
                    // In that case need to keep scanning and see if the next modified/added line available is still
                    // part of the declaration or not.
                    if (isNaN(newN)) {
                        let forceFinish = false;
                        while (index < lineNumbers.length - 1) {
                            index += 1;
                            newN = lineNumbers[index].newN;
                            // Cycle until we find a added/modified line
                            if (!isNaN(newN)) {
                                // If the line is still part of the current method declaration,
                                // we continue scanning and get out of the innermost loop.
                                if (isWithinMethodDeclaration(mc, newN)) {
                                    end_index = index;
                                    break;
                                // If it's not, the current method declaration is over and
                                // we go back to the outermost loop.
                                } else {
                                    index -= 1;
                                    forceFinish = true;
                                    break;
                                }
                            }
                        }
                        if (forceFinish) break;
                    }
                    // If it's an added/normal line and it's still part of the declaration, go on scanning
                    else if (isWithinMethodDeclaration(mc, newN)) end_index = index;
                    // Otherwise break the declaration
                    else break;
                }
                // Push to the list the identified range and all its corresponding method calls
                file_methodcalls.callers.push({
                    start_index,
                    end_index,
                    method_calls
                });
            }
            index += 1;
        }

        // If the file has at least one caller or callee, add its method calls to the object
        // that keeps them organized along with a reference to the file
        if (Object.keys(file_methodcalls.callees).length || Object.keys(file_methodcalls.callers).length) {
            organizedMethodCalls[modified_filename] = {
                reference,
                method_calls: file_methodcalls
            };
        }
    }

    return organizedMethodCalls;
}

// Given the review ID, display the method calls in the sidebars
const displayMethodCalls = (review_id) => {
    // Compute the map used to associate the line numbers in the diffs to the method calls
    const map = computeMap();

    for (const [filePath, { lineNumbers, reference }] of Object.entries(map)) {
        const newlinenumbers = lineNumbers.map(({ newN }) => newN).filter(n => !isNaN(n));
        let firstNewN, lastNewN;
        if (!newlinenumbers.length) {
            firstNewN = -1;
            lastNewN = -1;
        } else {
            firstNewN = newlinenumbers[0];
            lastNewN = newlinenumbers[newlinenumbers.length - 1];
        }
        reference.find('.d2h-file-name').wrap('<a href="#"></a>');
        reference
            .find('.d2h-file-name')
            .on('click', (e) => {
                // Do not actually submit anything
                e.preventDefault();
                // Show modal containing method declaration
                popupClass(
                    review_id,
                    'diff',
                    filePath,
                    firstNewN,
                    lastNewN
                );
        });
    }

    // Revmove 'loading' spinner/text
    $('tr:first-child > td.callsx, tr:first-child > td.calldx').removeClass('loading');
    $('tr:first-child > td.callsx, tr:first-child > td.calldx').addClass('header');
    $('tr:first-child > td.callsx').html('CALLERS');
    $('tr:first-child > td.calldx').html('CALLEES');

    // Retrieve method calls via AJAX from backend
    const methodCallsURL = `${backendURL}/review/${review_id}/methodcalls`;
    $.getJSON(methodCallsURL)
        .done((data) => {
            // Organize the callees and callers per file, and per line (callees) in a dictionary (=object)
            const organized_methodcalls = organizeMethodCalls(data.data, map);

            // Iterate over each of the modified files (that contain at least 1 caller or callee to display)
            for (const [filename, { method_calls: f_method_calls, reference }] of Object.entries(organized_methodcalls)) {
                // Iterate over the callers of the current file
                for (const { start_index, end_index, method_calls } of f_method_calls.callers) {
                    // Find the row and cell corresponding to the start of the caller range
                    const first_cell = reference.find(`tr:nth-child(${start_index+1})`).find('td.callsx');
                    if (start_index === 0) first_cell.addClass('top');
                    if (end_index === map[filename].lineNumbers.length - 1) first_cell.addClass('bottom');
                    // Add a scrollable div and a bullet list inside it
                    first_cell.append('<div class="scrollable"><ul class="fcell"></ul></div>');
                    // Compute number of rows taken by caller
                    const nrows = end_index-start_index+1;
                    // Max length of the qualifier shown
                    const max_length = 27;
                    // Iterate over all the caller referring to the same method declaration
                    for (const mc of method_calls) {
                        // Deduct if the file containing the method call has been modified or not,
                        // changes how it is shown once the user clicks on the method call
                        const type = map.hasOwnProperty(mc.from_file) ? 'diff' : 'normal';
                        // Truncate method call qualifier to fit in the sidebar
                        const enr_mc = `└ ${mc.method_call}`;
                        const truncated_qual = enr_mc.length > max_length ?
                            enr_mc.substr(0, max_length - 2) + '..' :
                            enr_mc;
                        // Append anchor representing method call
                        const splitted_from_file = mc.from_file.split('/');
                        const from_file_name = splitted_from_file[splitted_from_file.length - 1];
                        const enriched_from_filename = type === 'diff' ? `✎ ${from_file_name}` : from_file_name;
                        const linenumber = `${mc.call_start_line}`;
                        const linenumber_len = linenumber.length;
                        const max_filename_length = max_length - linenumber_len - 2;
                        const truncated_filename = enriched_from_filename.length > max_filename_length ?
                            enriched_from_filename.substr(0, max_filename_length - 2) + '..' :
                            enriched_from_filename;
                        const upper = `${truncated_filename}:L${linenumber}`;
                        const lower = truncated_qual;
                        const upper_lower = `<ul class="uplow"><li>${upper}<li><strong>${lower}</strong></ul>`;
                        const anchor = `<a href='#' title='In ${mc.from_file}:L${mc.call_start_line}\n\n${mc.method_call}'>${upper_lower}</a>`;
                        first_cell
                            .find('ul.fcell')
                            .append(`<li>${anchor}`);
                        // Add onclick listener to the method call link to open the file containing the caller
                        first_cell.find('li:last-child > a').on('click', (e) => {
                            // Do not actually submit anything
                            e.preventDefault();
                            // Show modal containing method declaration
                            popupClass(
                                review_id,
                                type,
                                mc.from_file,
                                mc.call_start_line,
                                mc.call_end_line
                            );
                        });
                    }

                    // Make cell span multiple rows (= to declaration range)
                    first_cell.attr('rowspan', nrows);
                    first_cell.addClass('caller');

                    // Remove cells below the caller range that are 'eaten' by the rowspan
                    for (let i = start_index + 1; i <= end_index; i += 1) {
                        reference.find(`tr:nth-child(${i+1})`).find('td.callsx').remove();
                    }
                }

                // Iterate over the callees of the current file
                for (const { newN: line, callees: line_callees } of f_method_calls.callees) {
                    // Find the line in the diff where we are supposed to show the callee
                    const td_cell = reference.find(`.line-num2`)
                        .filter(function() { return $(this).text() == line; })
                        .parent();
                    // Once we've found the line we walk the dom to find the cell in the right sidebar
                    const mc_cell = td_cell.parent().find('.calldx');
                    mc_cell.addClass('callee');
                    mc_cell.html(`<ul class='fcell'></ul>`);
                    // Modify the style of the cell so that it still looks good even if there are multiple callers
                    td_cell.attr('style', `height: ${20 * line_callees.length}px; padding-top: ${10 * (line_callees.length-1)}px`);
                    // Iterate over the callees of the lien
                    for (const method_call of line_callees) {
                        // Similar to caller display, see above
                        const type = map.hasOwnProperty(method_call.to_file) ? 'diff' : 'normal';
                        const max_length = 29;

                        const enr_mc = `└ ${method_call.short_method_qualifier}`;
                        const truncated_qual = enr_mc.length > max_length ?
                            enr_mc.substr(0, max_length - 2) + '..' :
                            enr_mc;
                        const splitted_to_file = method_call.to_file.split('/');
                        const to_file_name = splitted_to_file[splitted_to_file.length - 1];
                        const enriched_to_filename = type === 'diff' ? `✎ ${to_file_name}` : to_file_name;
                        const linenumbers = `L${method_call.declaration_start_line}-${method_call.declaration_end_line}`;
                        const linenumbers_len = linenumbers.length;
                        const max_filename_length = max_length - linenumbers_len - 1;
                        const truncated_filename = enriched_to_filename.length > max_filename_length ?
                            enriched_to_filename.substr(0, max_filename_length - 2) + '..' :
                            enriched_to_filename;
                        const upper = `${truncated_filename}:${linenumbers}`;
                        const lower = truncated_qual;
                        const upper_lower = `<ul class="uplow"><li>${upper}<li><strong>${lower}</strong></ul>`;
                        const anchor = `<a href='#' title='In ${method_call.to_file}:L${method_call.declaration_start_line}-` +
                            `${method_call.declaration_end_line}\n\n${method_call.full_method_qualifier}'` +
                            `>${upper_lower}</a>`;
                        mc_cell
                            .find('ul.fcell')
                            .append(`<li>${anchor}`);

                        // Add onclick event to open modal containing source of the method
                        mc_cell.find('li:last-child > a').on('click', (e) => {
                            // Do not actually submit anything
                            e.preventDefault();
                            // Show modal containing method declaration
                            popupClass(
                                review_id,
                                type,
                                method_call.to_file,
                                method_call.declaration_start_line,
                                method_call.declaration_end_line
                            );
                        });
                    }
                }
            }
        });
};

// Execute operations after the opening of the modal
$('#modal-source').on('shown.bs.modal', function (e) {
    // Get start and end line range data attributes
    const start_line = $(this).data('startline');
    const end_line = $(this).data('endline');
    // Instead of scrolling the source code exactly to the start of the method declaration (or method call),
    // we start slightly above to give some context
    const slack = 3;
    const first_shown_line = start_line > slack ? start_line - slack : start_line;
    if (start_line != -1 && end_line != -1) {
        // If the file to be shown was not modified in the PR under analysis
        if ($(this).data('type') === 'normal') {
            $('code').each(function(i, block) {
                // The line number plugin is async.. timeout is needed before scrolling because of this
                setTimeout(() => {
                    // Mark the relevant lines so that we can style them accordingly
                    for (let line_n = start_line; line_n <= end_line; line_n += 1) {
                        $(`code > table > tbody > tr:nth-child(${line_n})`).addClass('relevant');
                    }
                    // Scroll to position of first line to show
                    const $scrollTo = $(`code > table > tbody > tr:nth-child(${first_shown_line})`);
                    const $container = $('.modal-body');
                    $container.animate({ scrollTop: $scrollTo.offset().top - $container.offset().top + $container.scrollTop() });
                }, 200);
            });
        // If the file to be shown was modified in the PR under analysis
        } else {
            // Scroll to position of first line to show
            const $scrollTo = $(this).find('.line-num2')
                .filter(function() { return parseInt($(this).text(), 10) === first_shown_line; });
            const $container = $('.modal-body');
            $container.animate({ scrollTop: $scrollTo.offset().top - $container.offset().top + $container.scrollTop() });
        }
    }
});


