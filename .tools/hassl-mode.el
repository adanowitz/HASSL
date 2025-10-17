;;; hassl-mode.el --- Major mode for HASSL DSL -*- lexical-binding: t; -*-

;; Author: You
;; Version: 0.2
;; Keywords: languages
;; Package-Requires: ((emacs "26.1"))

;;; Commentary:
;; Major mode for HASSL files.
;; - Highlights package/import/private/alias/schedule/rule/etc.
;; - Simple indentation for schedule/rule/if/then blocks
;; - Imenu: lists rules and schedules
;; - Electric semicolon: only “active” inside THEN actions and SCHEDULE clauses

;;; Code:

;; ---------- Customization ----------
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
    ;; # comments
    (modify-syntax-entry ?# "<" st)
    (modify-syntax-entry ?\n ">" st)
    ;; strings
    (modify-syntax-entry ?\" "\"" st)
    ;; treat _ and . as word constituents to keep entity ids together
    (modify-syntax-entry ?_ "w" st)
    (modify-syntax-entry ?. "w" st)
    st)
  "Syntax table for `hassl-mode'.")

;; ---------- Font lock ----------
(defconst hassl--keywords
  '("package" "import" "private" "alias" "rule" "if" "then" "wait" "for"
    "schedule" "use" "enable" "disable" "from" "to" "until" "as"
    "sunrise" "sunset"
    "SCHEDULE" "USE" "ENABLE" "DISABLE" "FROM" "TO" "UNTIL"))

(defconst hassl--booleans '("on" "off" "true" "false"))

(defconst hassl--keywords-re (regexp-opt hassl--keywords 'symbols))
(defconst hassl--booleans-re (regexp-opt hassl--booleans 'symbols))
(defconst hassl--units-re "\\b\$begin:math:text$[0-9]+\\$end:math:text$\$begin:math:text$?:ms\\\\|s\\\\|m\\\\|h\\\\|d\\$end:math:text$\\b")
(defconst hassl--entity-re "\\b[[:alpha:]_][[:alnum:]_]*\\.[[:alnum:]_]+\\b")
(defconst hassl--rule-hdr-re "^\\s-*rule\\s-+\$begin:math:text$[^:\\n]+\\$end:math:text$\\s-*:?\\s-*$")
(defconst hassl--schedule-hdr-re "^\\s-*schedule\\s-+\$begin:math:text$[^:\\n]+\\$end:math:text$\\s-*:?\\s-*$")
(defconst hassl--alias-name-re
  "^\\s-*\$begin:math:text$?:private\\\\s-+\\$end:math:text$?alias\\s-+\$begin:math:text$[A-Za-z0-9_]+\\$end:math:text$\\s-*=")

(defconst hassl-font-lock-keywords
  `(
    (,hassl--keywords-re . font-lock-keyword-face)
    (,hassl--booleans-re . font-lock-constant-face)
    (,hassl--units-re . font-lock-number-face)
    (,hassl--entity-re . font-lock-variable-name-face)
    (,hassl--rule-hdr-re  (1 font-lock-function-name-face))
    (,hassl--schedule-hdr-re (1 font-lock-function-name-face))
    (,hassl--alias-name-re (1 font-lock-variable-name-face))
    ))

;; ---------- Imenu ----------
(defconst hassl-imenu-generic-expression
  `(("Rules" ,hassl--rule-hdr-re 1)
    ("Schedules" ,hassl--schedule-hdr-re 1)))

;; ---------- Helpers: context detection ----------
(defun hassl--in-then-block-p ()
  "Return non-nil if point is inside a THEN action list."
  (save-excursion
    (let ((pos (point))
          (case-fold-search t))
      (and (re-search-backward "^\\s-*then\\b" nil t)
           (not (re-search-forward "^\\s-*\$begin:math:text$rule\\\\|schedule\\$end:math:text$\\b" pos t))))))

(defun hassl--in-schedule-clauses-p ()
  "Return non-nil if point is inside a SCHEDULE clause block (after header with ':')."
  (save-excursion
    (let ((pos (point))
          (case-fold-search t))
      (and (re-search-backward "^\\s-*schedule\\s-+[^:\n]+:\\s-*$" nil t)
           (not (re-search-forward "^\\s-*\$begin:math:text$rule\\\\|schedule\\\\|package\\\\|import\\\\|alias\\\\|private\\$end:math:text$\\b" pos t))))))

(defun hassl--semicolon-required-here-p ()
  "Is semicolon meaningful at point (THEN or SCHEDULE clauses)?"
  (or (hassl--in-then-block-p)
      (hassl--in-schedule-clauses-p)))

;; ---------- Electric semicolon ----------
(defun hassl-electric-semicolon (arg)
  "Insert ';'. Only \"required\" inside THEN actions or SCHEDULE clauses."
  (interactive "p")
  (dotimes (_ (or arg 1))
    (insert ";"))
  (unless (hassl--semicolon-required-here-p)
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
     ((or (hassl--line-starts-with "package")
          (hassl--line-starts-with "import")
          (hassl--line-starts-with "alias")
          (hassl--line-starts-with "private")
          (hassl--line-starts-with "rule")
          (hassl--line-starts-with "schedule"))
      0)
     ((or (hassl--in-then-block-p)
          (hassl--in-schedule-clauses-p))
      hassl-indent-offset)
     ((or (hassl--line-starts-with "if")
          (hassl--line-starts-with "then"))
      hassl-indent-offset)
     (t
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
  (electric-indent-local-mode 1))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.hassl\\'" . hassl-mode))

(provide 'hassl-mode)
;;; hassl-mode.el ends here
