;;; hassl-mode.el --- Major mode for HASSL DSL -*- lexical-binding: t; -*-

;; Author: You
;; Version: 0.1
;; Keywords: languages
;; Package-Requires: ((emacs "26.1"))
;; URL: https://example.invalid/hassl-mode

;;; Commentary:
;; Major mode for HASSL files.
;; - Highlights package/import/private/alias/schedule/rule/etc.
;; - Simple indentation for schedule/rule/if/then blocks
;; - Imenu: lists rules and schedules
;; - Electric semicolon: only “required/active” inside THEN blocks and SCHEDULE clauses

;;; Code:

(require 'rx)

(defgroup hassl nil
  "HASSL DSL support."
  :group 'languages)

(defcustom hassl-indent-offset 2
  "Indentation step for HASSL."
  :type 'integer
  :group 'hassl)

;; ---------- Syntax table ----------
(defvar hassl-mode-syntax-table
  (let ((st (make-syntax-table)))
    ;; comment: # to end of line
    (modify-syntax-entry ?# "<" st)
    (modify-syntax-entry ?\n ">" st)
    ;; strings: "..."
    (modify-syntax-entry ?\" "\"" st)
    ;; underscore, dot are word constituents for entity ids
    (modify-syntax-entry ?_ "w" st)
    (modify-syntax-entry ?. "w" st)
    st)
  "Syntax table for `hassl-mode'.")

;; ---------- Font lock ----------
(defconst hassl--keywords
  '("package" "import" "private" "alias" "rule" "if" "then" "wait" "for"
    "schedule" "use" "enable" "disable" "from" "to" "until" "as"
    "sunrise" "sunset" "SCHEDULE" "USE" "ENABLE" "DISABLE" "FROM" "TO" "UNTIL"))

(defconst hassl--booleans '("on" "off" "true" "false"))
(defconst hassl--units '("ms" "s" "m" "h" "d"))

(defconst hassl-font-lock-keywords
  `(
    ;; Keywords
    (,(rx symbol-start (or ,@hassl--keywords) symbol-end) . font-lock-keyword-face)
    ;; Booleans
    (,(rx symbol-start (or ,@hassl--booleans) symbol-end) . font-lock-constant-face)
    ;; Units in durations like 10m, 500ms
    (,(rx symbol-start (+ digit) (or ,@hassl--units) symbol-end) . font-lock-number-face)
    ;; Entity IDs like light.kitchen_lamp
    (,(rx symbol-start (+ (any "a-zA-Z_")) "." (+ (any "a-zA-Z0-9_"))) . font-lock-variable-name-face)
    ;; Rule / schedule names on headers
    (,(rx line-start (* space) (or "rule" "schedule") (+ space)
          (group (+ (not (any ":\n")))) (* space) (optional ":"))
     1 font-lock-function-name-face)
    ;; Alias name in: alias NAME = entity
    (,(rx line-start (* space) (optional "private" (+ space)) "alias" (+ space)
          (group (+ (any "a-zA-Z0-9_"))) (* space) "=")
     1 font-lock-variable-name-face)
    ))

;; ---------- Imenu ----------
(defconst hassl-imenu-generic-expression
  `(("Rules" ,(rx line-start (* space) "rule" (+ space)
                  (group (+ (not (any ":\n")))))
     1)
    ("Schedules" ,(rx line-start (* space) "schedule" (+ space)
                      (group (+ (not (any ":\n")))))
     1)))

;; ---------- Helpers: context detection ----------
(defun hassl--in-then-block-p ()
  "Return non-nil if point is inside a THEN action list."
  (save-excursion
    (let ((pos (point))
          (case-fold-search t))
      (and (re-search-backward (rx line-start (* space) "then" word-end) nil t)
           (let ((then-pos (point)))
             ;; If there's a new rule/schedule header after THEN, we’re out.
             (not (re-search-forward
                   (rx line-start (* space) (or "rule" "schedule") word-end) pos t)))))))

(defun hassl--in-schedule-clauses-p ()
  "Return non-nil if point is inside a SCHEDULE clause block (after header with ':')."
  (save-excursion
    (let ((pos (point))
          (case-fold-search t))
      (and (re-search-backward (rx line-start (* space) "schedule" (+ space)
                                   (+ (not (any "\n"))) (* space) ":" (* space) eol)
                               nil t)
           ;; Stop if we encounter a new header before POS
           (not (re-search-forward
                 (rx line-start (* space) (or "rule" "schedule" "package" "import" "alias" "private") word-end)
                 pos t))))))

(defun hassl--semicolon-required-here-p ()
  "Is semicolon syntactically required/meaningful at point?
We treat it as required only in THEN action lists and SCHEDULE clause lists."
  (or (hassl--in-then-block-p)
      (hassl--in-schedule-clauses-p)))

;; ---------- Electric semicolon ----------
(defun hassl-electric-semicolon (arg)
  "Insert ';'. If in a context where semicolons are required (THEN or SCHEDULE),
just insert it; otherwise insert and maybe show a hint."
  (interactive "p")
  (dotimes (_ (or arg 1))
    (insert ";"))
  (when (not (hassl--semicolon-required-here-p))
    (message "Note: ';' is only required inside THEN actions or SCHEDULE clauses.")))

;; ---------- Indentation ----------
(defun hassl--line-starts-with (kw)
  (save-excursion
    (back-to-indentation)
    (looking-at (concat "\\_<" (regexp-quote kw) "\\_>"))))

(defun hassl--previous-nonblank-indentation ()
  (save-excursion
    (forward-line -1)
    (while (and (not (bobp))
                (looking-at-p "^[ \t]*$"))
      (forward-line -1))
    (current-indentation)))

(defun hassl-calculate-indentation ()
  "Compute indentation for current line."
  (save-excursion
    (back-to-indentation)
    (cond
     ;; Top-level headers align to column 0
     ((or (hassl--line-starts-with "package")
          (hassl--line-starts-with "import")
          (hassl--line-starts-with "alias")
          (hassl--line-starts-with "private")
          (hassl--line-starts-with "rule")
          (hassl--line-starts-with "schedule"))
      0)
     ;; Lines directly under 'then' or within schedule clause: indent
     ((save-excursion
        (let ((case-fold-search t)
              (pos (point)))
          (or (hassl--in-then-block-p)
              (hassl--in-schedule-clauses-p))))
      hassl-indent-offset)
     ;; 'if' and 'then' often sit flush under rule header (indent one)
     ((or (hassl--line-starts-with "if")
          (hassl--line-starts-with "then"))
      hassl-indent-offset)
     (t
      ;; Default: keep previous nonblank indentation
      (hassl--previous-nonblank-indentation)))))

(defun hassl-indent-line ()
  "Indent current line as HASSL."
  (interactive)
  (let ((col (hassl-calculate-indentation))
        (pos (- (current-column) (current-indentation))))
    (indent-line-to col)
    (when (> pos 0) (move-to-column (+ col pos)))))

;; ---------- Mode definition ----------
(defvar hassl-mode-map
  (let ((m (make-sparse-keymap)))
    (define-key m (kbd ";") #'hassl-electric-semicolon)
    m)
  "Keymap for `hassl-mode'.")

;;;###autoload
(define-derived-mode hassl-mode prog-mode "HASSL"
  "Major mode for editing HASSL DSL."
  :syntax-table hassl-mode-syntax-table
  (setq-local font-lock-defaults '(hassl-font-lock-keywords))
  (setq-local indent-line-function #'hassl-indent-line)
  (setq-local comment-start "#")
  (setq-local comment-start-skip "#+\\s-*")
  (setq-local imenu-generic-expression hassl-imenu-generic-expression)
  ;; Treat semicolon as punctuation, but we handle its semantics in `hassl-electric-semicolon'
  (electric-indent-local-mode 1))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.hassl\\'" . hassl-mode))

(provide 'hassl-mode)
;;; hassl-mode.el ends here
