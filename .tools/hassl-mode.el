;;; hassl-mode.el --- Major mode for HASSL DSL -*- lexical-binding: t; -*-

;; Author: You
;; Version: 0.5
;; Keywords: languages
;; Package-Requires: ((emacs "26.1"))

;;; Commentary:
;; Major mode for HASSL files.
;; - Highlights modules, templates, rules, schedules, timed clauses, and events
;; - Supports clock, sunrise/sunset, schedule transitions, holidays, and events
;; - Imenu lists templates, rules, schedules, and holiday sets
;; - Electric semicolon is context-aware for action and schedule clauses

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
    ;; Accept both documented # comments and parser-native // comments.
    (modify-syntax-entry ?# "< b" st)
    (modify-syntax-entry ?/ ". 124b" st)
    (modify-syntax-entry ?\n "> b" st)
    (modify-syntax-entry ?\" "\"" st)
    ;; Keep aliases and Home Assistant entity IDs together as symbols.
    (modify-syntax-entry ?_ "w" st)
    (modify-syntax-entry ?. "w" st)
    st)
  "Syntax table for `hassl-mode'.")

;; ---------- Font lock ----------
(defconst hassl--keywords
  '("package" "import" "private" "alias" "sync"
    "onoff" "dimmer" "attribute" "shared" "all"
    "template" "use" "rule" "if" "is" "then" "at" "wait" "for"
    "arm" "when" "not_by" "this" "any_hassl" "tag"
    "schedule" "enable" "disable" "from" "to" "until" "as"
    "during" "months" "dates" "range" "on" "weekdays" "weekends"
    "daily" "except" "holidays" "country" "province" "workdays"
    "excludes" "add" "remove" "sunrise" "sunset"))

(defconst hassl--booleans '("on" "off" "true" "false"))

(defconst hassl--event-gestures
  '("pressed" "clicked" "held" "hold_released"
    "multi_pressing" "multi_pressed"))

(defconst hassl--keywords-re (regexp-opt hassl--keywords 'symbols))
(defconst hassl--booleans-re (regexp-opt hassl--booleans 'symbols))
(defconst hassl--event-gestures-re
  (regexp-opt hassl--event-gestures 'symbols))
(defconst hassl--schedule-transition-re
  "\\_<schedule\\_>\\s-+\\(start\\|stop\\)\\_>"
  "Match the transition in an `at schedule start/stop' clause.")
(defconst hassl--units-re
  "\\b[0-9]+\\(?:ms\\|s\\|m\\|h\\|d\\)\\b")
(defconst hassl--clock-re
  "\\b\\(?:[01]?[0-9]\\|2[0-3]\\):[0-5][0-9]\\b")
(defconst hassl--entity-re
  "\\_<[[:alpha:]_][[:alnum:]_]*\\(?:\\.[[:alnum:]_]+\\)+\\_>")
(defconst hassl--rule-hdr-re
  "^\\s-*rule\\s-+\\([^:\n]+\\)\\s-*:\\s-*$")
(defconst hassl--schedule-hdr-re
  "^\\s-*schedule\\s-+\\([^:\n]+\\)\\s-*:\\s-*$")
(defconst hassl--holidays-hdr-re
  "^\\s-*holidays\\s-+\\([^:\n]+\\)\\s-*:\\s-*$")
(defconst hassl--template-hdr-re
  (concat "^\\s-*\\(?:private\\s-+\\)?template\\s-+"
          "\\(?:rule\\|sync\\|schedule\\)\\s-+"
          "\\([A-Za-z_][A-Za-z0-9_]*\\)"))
(defconst hassl--alias-name-re
  (concat "^\\s-*\\(?:private\\s-+\\)?alias\\s-+"
          "\\([A-Za-z_][A-Za-z0-9_]*\\)\\s-*="))

(defconst hassl-font-lock-keywords
  `((,hassl--keywords-re . font-lock-keyword-face)
    (,hassl--schedule-transition-re (1 font-lock-builtin-face))
    (,hassl--booleans-re . font-lock-constant-face)
    (,hassl--event-gestures-re . font-lock-builtin-face)
    (,hassl--units-re . font-lock-number-face)
    (,hassl--clock-re . font-lock-constant-face)
    (,hassl--entity-re . font-lock-variable-name-face)
    (,hassl--rule-hdr-re (1 font-lock-function-name-face))
    (,hassl--schedule-hdr-re (1 font-lock-function-name-face))
    (,hassl--holidays-hdr-re (1 font-lock-function-name-face))
    (,hassl--template-hdr-re (1 font-lock-function-name-face))
    (,hassl--alias-name-re (1 font-lock-variable-name-face))))

;; ---------- Imenu ----------
(defconst hassl-imenu-generic-expression
  `(("Templates" ,hassl--template-hdr-re 1)
    ("Rules" ,hassl--rule-hdr-re 1)
    ("Schedules" ,hassl--schedule-hdr-re 1)
    ("Holiday sets" ,hassl--holidays-hdr-re 1)))

;; ---------- Context detection ----------
(defconst hassl--top-level-re
  (concat "^\\s-*\\(?:"
          "package\\_>\\|import\\_>\\|"
          "\\(?:private\\s-+\\)?alias\\_>\\|"
          "sync\\_>\\|rule\\_>\\|holidays\\_>\\|"
          "\\(?:private\\s-+\\)?template\\_>\\|"
          "use\\s-+template\\_>\\|"
          "schedule\\s-+[^[:space:]:]+\\s-*:\\)"))

(defun hassl--in-then-block-p ()
  "Return non-nil if point follows a THEN action in the current declaration."
  (save-excursion
    (let ((pos (point))
          (case-fold-search nil))
      (and (re-search-backward "\\_<then\\_>" nil t)
           (not (re-search-forward hassl--top-level-re pos t))))))

(defun hassl--in-schedule-clauses-p ()
  "Return non-nil if point is inside a named schedule clause block."
  (save-excursion
    (let ((pos (point))
          (case-fold-search nil))
      (and (re-search-backward hassl--schedule-hdr-re nil t)
           (progn
             (goto-char (match-end 0))
             (not (re-search-forward hassl--top-level-re pos t)))))))

(defun hassl--semicolon-required-here-p ()
  "Return non-nil when a semicolon is meaningful at point."
  (or (hassl--in-then-block-p)
      (hassl--in-schedule-clauses-p)))

;; ---------- Electric semicolon ----------
(defun hassl-electric-semicolon (arg)
  "Insert ARG semicolons and report when they are optional in HASSL."
  (interactive "p")
  (dotimes (_ (or arg 1))
    (insert ";"))
  (unless (hassl--semicolon-required-here-p)
    (message "HASSL: ';' is only needed between actions or schedule clauses.")))

;; ---------- Indentation ----------
(defun hassl--line-starts-with (keyword)
  "Return non-nil if the current line starts with KEYWORD."
  (save-excursion
    (back-to-indentation)
    (looking-at (concat "\\_<" (regexp-quote keyword) "\\_>"))))

(defun hassl--previous-nonblank-indentation ()
  "Return indentation of the previous nonblank line."
  (save-excursion
    (forward-line -1)
    (while (and (not (bobp))
                (looking-at-p "^[ \t]*$"))
      (forward-line -1))
    (current-indentation)))

(defun hassl--top-level-declaration-p ()
  "Return non-nil if the current line begins a top-level declaration."
  (save-excursion
    (back-to-indentation)
    (looking-at-p hassl--top-level-re)))

(defun hassl-calculate-indentation ()
  "Compute indentation for the current HASSL line."
  (save-excursion
    (back-to-indentation)
    (let ((open-paren (nth 1 (syntax-ppss))))
      (cond
       (open-paren
        (if (looking-at-p "[])}]")
            (save-excursion
              (goto-char open-paren)
              (current-indentation))
          (save-excursion
            (goto-char open-paren)
            (+ (current-indentation) hassl-indent-offset))))
       ((hassl--top-level-declaration-p) 0)
       ((or (hassl--line-starts-with "schedule")
            (hassl--line-starts-with "arm")
            (hassl--line-starts-with "if")
            (hassl--line-starts-with "at")
            (hassl--line-starts-with "then"))
        hassl-indent-offset)
       ((or (hassl--in-then-block-p)
            (hassl--in-schedule-clauses-p))
        hassl-indent-offset)
       (t (hassl--previous-nonblank-indentation))))))

(defun hassl-indent-line ()
  "Indent the current line as HASSL."
  (interactive)
  (let ((column (hassl-calculate-indentation))
        (position (- (current-column) (current-indentation))))
    (indent-line-to column)
    (when (> position 0)
      (move-to-column (+ column position)))))

;; ---------- Mode definition ----------
(defvar hassl-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map (kbd ";") #'hassl-electric-semicolon)
    map)
  "Keymap for `hassl-mode'.")

;;;###autoload
(define-derived-mode hassl-mode prog-mode "HASSL"
  "Major mode for editing HASSL DSL."
  :syntax-table hassl-mode-syntax-table
  (setq-local font-lock-defaults '(hassl-font-lock-keywords))
  (setq-local indent-line-function #'hassl-indent-line)
  (setq-local comment-start "// ")
  (setq-local comment-start-skip "\\(?://+\\|#+\\)\\s-*")
  (setq-local imenu-generic-expression hassl-imenu-generic-expression)
  (electric-indent-local-mode 1))

;;;###autoload
(add-to-list 'auto-mode-alist '("\\.hassl\\'" . hassl-mode))

(provide 'hassl-mode)
;;; hassl-mode.el ends here
