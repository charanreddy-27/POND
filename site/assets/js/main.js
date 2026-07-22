/* POND — site behaviour. No dependencies, no build step. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------------- theme */

  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    // The effective theme is whatever `color-scheme` actually resolved to —
    // covers the explicit data-theme and the system default in one read.
    var effective = function () {
      return getComputedStyle(document.documentElement).colorScheme === "light" ? "light" : "dark";
    };
    var label = function () {
      toggle.setAttribute(
        "aria-label",
        "Switch to " + (effective() === "dark" ? "light" : "dark") + " theme"
      );
    };

    label(); // the markup's guess is only right half the time
    window
      .matchMedia("(prefers-color-scheme: light)")
      .addEventListener("change", function () {
        if (!document.documentElement.hasAttribute("data-theme")) label();
      });

    toggle.addEventListener("click", function () {
      var next = effective() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      label();
      try {
        localStorage.setItem("pond-theme", next);
      } catch (e) {
        /* private mode — the choice just won't persist */
      }
    });
  }

  /* -------------------------------------------------------- copy to clipboard */

  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var pre = btn.parentElement.querySelector("pre");
      if (!pre) return;
      var text = pre.innerText.replace(/^\$ /gm, "").trim();
      var done = function () {
        btn.setAttribute("data-copied", "true");
        btn.setAttribute("aria-label", "Copied");
        setTimeout(function () {
          btn.removeAttribute("data-copied");
          btn.setAttribute("aria-label", "Copy to clipboard");
        }, 1800);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, function () {});
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try {
          document.execCommand("copy");
          done();
        } catch (e) {
          /* nothing sensible to do */
        }
        document.body.removeChild(ta);
      }
    });
  });

  /* -------------------------------------------------------- scroll reveal */

  var revealables = document.querySelectorAll("[data-reveal]");
  if (revealables.length) {
    if (reduceMotion || !("IntersectionObserver" in window)) {
      revealables.forEach(function (el) {
        el.classList.add("is-visible");
      });
    } else {
      var io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add("is-visible");
              io.unobserve(entry.target);
            }
          });
        },
        { rootMargin: "0px 0px -8% 0px", threshold: 0.05 }
      );
      revealables.forEach(function (el) {
        io.observe(el);
      });
    }
  }

  /* ------------------------------------------------------- contact form */

  var form = document.getElementById("contact-form");
  if (form) {
    var status = form.querySelector(".form-status");
    var setInvalid = function (field, invalid) {
      field.classList.toggle("is-invalid", invalid);
      var input = field.querySelector("input, textarea");
      if (input) input.setAttribute("aria-invalid", invalid ? "true" : "false");
    };

    form.querySelectorAll("input, textarea").forEach(function (input) {
      input.addEventListener("input", function () {
        var field = input.closest(".form-field");
        if (field && field.classList.contains("is-invalid") && input.checkValidity()) {
          setInvalid(field, false);
        }
      });
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var ok = true;
      form.querySelectorAll("input, textarea").forEach(function (input) {
        var field = input.closest(".form-field");
        if (!field) return;
        var valid = input.checkValidity();
        setInvalid(field, !valid);
        if (!valid && ok) {
          input.focus();
          ok = false;
        }
      });
      if (!ok) {
        status.textContent = "Almost — a couple of fields need attention.";
        return;
      }

      // No backend by design: this hands off to the visitor's own mail client.
      var name = form.elements.name.value.trim();
      var email = form.elements.email.value.trim();
      var message = form.elements.message.value.trim();
      var body = message + "\n\n—\n" + name + "\n" + email;
      status.textContent = "Opening your mail client…";
      window.location.href =
        "mailto:" +
        form.dataset.mailto +
        "?subject=" +
        encodeURIComponent("Hello from POND — " + name) +
        "&body=" +
        encodeURIComponent(body);
    });
  }

  /* ------------------------------------------------------ terminal replay */

  var term = document.getElementById("terminal-body");
  if (!term) return;

  // Every frame is pre-formatted for a monospace grid. `cls` maps to a colour
  // role in styles.css; `text` is emitted verbatim inside a <pre> context.
  var DEMOS = [
    {
      chip: "spend vs. gym",
      cmd: 'pond ask "how much did I spend on Swiggy in weeks I skipped gym this year?" --explain',
      out: [
        { cls: "", text: "" },
        { cls: "sql", text: "SELECT w.week_start, sum(-t.amount) AS swiggy_spend" },
        { cls: "sql", text: "FROM weeks w" },
        { cls: "sql", text: "JOIN transactions t" },
        { cls: "sql", text: "  ON date_trunc('week', t.ts)::DATE = w.week_start" },
        { cls: "sql", text: "WHERE w.went_to_gym = FALSE" },
        { cls: "sql", text: "  AND t.merchant = 'SWIGGY' AND t.amount < 0" },
        { cls: "sql", text: "GROUP BY 1 ORDER BY 1 LIMIT 500" },
        { cls: "", text: "" },
        { cls: "head", text: "  week_start   swiggy_spend" },
        { cls: "rule", text: "  ──────────   ────────────" },
        { cls: "num", text: "  2026-02-16       1,240.00" },
        { cls: "num", text: "  2026-04-06       2,115.00" },
        { cls: "num", text: "  2026-05-25         890.00" },
        { cls: "", text: "" },
        { cls: "dim", text: "3 rows · swiggy_spend: total 4,245.00 · avg 1,415.00" }
      ]
    },
    {
      chip: "what I listened to",
      cmd: 'pond ask "top 5 artists by hours listened last month"',
      out: [
        { cls: "", text: "" },
        { cls: "head", text: "  artist                     hours" },
        { cls: "rule", text: "  ──────                     ─────" },
        { cls: "num", text: "  Khruangbin                 14.20" },
        { cls: "num", text: "  Anoushka Shankar            9.75" },
        { cls: "num", text: "  Fred again..                8.10" },
        { cls: "num", text: "  Bonobo                      6.40" },
        { cls: "num", text: "  Tame Impala                 5.95" },
        { cls: "", text: "" },
        { cls: "dim", text: "5 rows · hours: total 44.40 · avg 8.88" }
      ]
    },
    {
      chip: "what's in the pond",
      cmd: "pond status",
      out: [
        { cls: "", text: "" },
        { cls: "dim", text: "                 ~/.pond/pond.duckdb" },
        { cls: "head", text: "  table                rows   from         to" },
        { cls: "rule", text: "  ─────                ────   ────         ──" },
        { cls: "", text: "  transactions        4,182   2023-01-04   2026-07-19" },
        { cls: "", text: "  messages           31,904   2019-06-11   2026-07-21" },
        { cls: "", text: "  listens            57,336   2021-03-02   2026-07-20" },
        { cls: "", text: "  activities            418   2024-01-08   2026-07-18" },
        { cls: "", text: "  daily_metrics         903   2024-01-08   2026-07-21" },
        { cls: "", text: "  searches           12,551   2022-11-30   2026-07-21" },
        { cls: "", text: "  youtube_watches     8,430   2022-11-30   2026-07-20" },
        { cls: "", text: "  calendar_events       671   2023-02-14   2026-09-02" },
        { cls: "", text: "" },
        { cls: "dim", text: "14 file(s) in the import ledger" }
      ]
    },
    {
      chip: "what the LLM sees",
      cmd: "pond schema",
      out: [
        { cls: "", text: "" },
        { cls: "sql", text: "CREATE TABLE transactions (" },
        { cls: "sql", text: "  ts TIMESTAMPTZ,          -- transaction date/time" },
        { cls: "sql", text: "  amount DECIMAL(12,2),    -- negative = spend" },
        { cls: "sql", text: "  narration VARCHAR,       -- raw bank description" },
        { cls: "sql", text: "  merchant VARCHAR,        -- e.g. 'SWIGGY'" },
        { cls: "sql", text: "  category VARCHAR         -- e.g. 'food_delivery'" },
        { cls: "sql", text: ");" },
        { cls: "dim", text: "-- … 7 more tables, 2 calendar-spine views" },
        { cls: "", text: "" },
        { cls: "head", text: "Known vocabulary (exact values in the data):" },
        { cls: "", text: "- transactions.category: food_delivery, fuel," },
        { cls: "", text: "    groceries, rent, salary, transport" },
        { cls: "", text: "- activities.activity_type: badminton, running," },
        { cls: "", text: "    strength_training" },
        { cls: "", text: "" },
        { cls: "dim", text: "vocab_sharing = 'categorical' — this text plus your" },
        { cls: "dim", text: "question is the entire payload; row data never" },
        { cls: "dim", text: "leaves the machine." }
      ]
    }
  ];

  var chipBar = document.getElementById("terminal-chips");
  var chipEls = [];
  var index = 0;
  var timers = [];
  var running = false;

  function clearTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function wait(ms, fn) {
    timers.push(setTimeout(fn, ms));
  }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function commandHTML(cmd, upto) {
    var shown = cmd.slice(0, upto);
    // Highlight the quoted question and any trailing --flags.
    var html = esc(shown)
      .replace(/(&quot;|")([^"]*)("|&quot;)?/, function (m) {
        return '<span class="q">' + m + "</span>";
      })
      .replace(/(--[a-z-]+)/g, '<span class="flag">$1</span>');
    return '<span class="prompt">$</span> ' + html;
  }

  function renderStatic(demo) {
    var html = commandHTML(demo.cmd, demo.cmd.length) + "\n";
    demo.out.forEach(function (line) {
      html += (line.cls ? '<span class="' + line.cls + '">' + esc(line.text) + "</span>" : esc(line.text)) + "\n";
    });
    term.innerHTML = html;
  }

  function selectChip(i) {
    chipEls.forEach(function (el, j) {
      el.setAttribute("aria-pressed", j === i ? "true" : "false");
    });
  }

  function play(i) {
    clearTimers();
    index = i;
    selectChip(i);
    var demo = DEMOS[i];

    if (reduceMotion) {
      renderStatic(demo);
      return;
    }

    var chars = 0;
    var lines = 0;

    function paint() {
      var html = commandHTML(demo.cmd, chars);
      if (chars < demo.cmd.length) {
        html += '<span class="caret"></span>';
      }
      html += "\n";
      for (var k = 0; k < lines; k++) {
        var line = demo.out[k];
        html +=
          (line.cls ? '<span class="' + line.cls + '">' + esc(line.text) + "</span>" : esc(line.text)) + "\n";
      }
      term.innerHTML = html;
    }

    function typeNext() {
      chars += 1;
      paint();
      if (chars < demo.cmd.length) {
        // Slight jitter so it reads like hands, not a metronome.
        wait(16 + Math.random() * 26, typeNext);
      } else {
        term.innerHTML = commandHTML(demo.cmd, chars) + '\n<span class="dim">thinking…</span>';
        wait(700, revealNext);
      }
    }

    function revealNext() {
      lines += 1;
      paint();
      if (lines < demo.out.length) {
        wait(demo.out[lines - 1].text === "" ? 40 : 70, revealNext);
      } else {
        wait(5200, function () {
          play((index + 1) % DEMOS.length);
        });
      }
    }

    paint();
    wait(320, typeNext);
  }

  DEMOS.forEach(function (demo, i) {
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    // Toggle buttons, not tabs: there are no tabpanels and no roving focus,
    // so aria-pressed is the honest role here.
    chip.setAttribute("aria-pressed", i === 0 ? "true" : "false");
    chip.textContent = demo.chip;
    chip.setAttribute("aria-label", "Show terminal example: " + demo.chip);
    chip.addEventListener("click", function () {
      running = true;
      play(i);
    });
    chipBar.appendChild(chip);
    chipEls.push(chip);
  });

  // Don't burn frames on a terminal nobody is looking at.
  function start() {
    if (running) return;
    running = true;
    play(0);
  }
  function stop() {
    running = false;
    clearTimers();
  }

  if (reduceMotion) {
    renderStatic(DEMOS[0]);
    selectChip(0);
  } else if ("IntersectionObserver" in window) {
    new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) start();
          else stop();
        });
      },
      { threshold: 0.15 }
    ).observe(term);
  } else {
    start();
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimers();
    } else if (running) {
      play(index);
    }
  });
})();
