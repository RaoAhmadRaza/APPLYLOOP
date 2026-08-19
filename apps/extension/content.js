// Greenhouse only (DEMO_PLAN §7). Fill, verify, report. Never submit.
//
// Two field classes, handled differently:
//  - CORE fields: Greenhouse's own ids, stable across every board — name, email,
//    phone, resume, cover letter.
//  - CUSTOM questions: a `question_<number>` id per board, generated per posting.
//    Only "LinkedIn" is mapped, by label text, because it's the one custom
//    question that recurs across boards and that the profile can answer. Every
//    other custom question — free text like "why do you want to work here" — has
//    no source of truth in the data dict, so it is always a HOLD. Never guessed.
//
// Select/combobox fields (Greenhouse's react-select "Where are you located?" /
// country picker) are out of scope for this pass: filling one correctly means
// driving an async, keyboard-searched popup, which is real work with its own
// failure modes. They're left as HOLDs — the honest answer, not a shortcut.

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "APPLYLOOP_FILL") return;
  fillForm(message.payload).then(sendResponse);
  return true; // keep the message channel open for the async response
});

// DEMO_PLAN §7 step 1 asks for this beyond the manifest's URL-pattern injection: the
// manifest only proves the *host* is Greenhouse, not that this particular page is an
// application form rather than, say, the board's listing page. `#application-form` is
// the one element every Greenhouse job-application page renders.
function isGreenhouseApplicationForm() {
  return document.getElementById("application-form") !== null;
}

async function fillForm(payload) {
  if (!isGreenhouseApplicationForm()) {
    return { error: "This page doesn't look like a Greenhouse application form." };
  }

  const filled = [];
  const holds = [];

  fillCore(payload.fields, filled, holds);
  await fillFiles(payload.documents, filled, holds);
  fillCustomQuestions(payload.fields, filled, holds);

  return { filled, holds };
}

function fillCore(fields, filled, holds) {
  const CORE = [
    ["first_name", fields.first_name],
    ["last_name", fields.last_name],
    ["email", fields.email],
    ["phone", fields.phone],
  ];
  for (const [id, value] of CORE) {
    const el = document.getElementById(id);
    if (!el) continue;
    const required = el.getAttribute("aria-required") === "true";
    if (value) {
      setNativeValue(el, value);
      verifyText(el, value, filled, holds, id);
    } else if (required) {
      hold(el, holds, id, "required, no value in the profile");
    }
  }
}

async function fillFiles(documents, filled, holds) {
  const FILES = [
    ["resume", "resume", true],
    ["cover_letter", "cover_letter", false],
  ];
  for (const [id, docKey, required] of FILES) {
    const el = document.getElementById(id);
    if (!el) continue;
    const doc = documents[docKey];
    if (doc) {
      const file = await dataUrlToFile(doc.dataUrl, doc.filename);
      setFile(el, file);
      if (el.files.length === 1 && el.files[0].name === doc.filename) {
        markFilled(el);
        filled.push(id);
      } else {
        hold(el, holds, id, "file did not verify after attach");
      }
    } else if (required) {
      hold(el, holds, id, "required, no document generated");
    }
  }
}

function fillCustomQuestions(fields, filled, holds) {
  for (const wrapper of document.querySelectorAll(".field-wrapper")) {
    const label = wrapper.querySelector("label");
    if (!label) continue;
    const id = label.getAttribute("for");
    if (!id || document.getElementById(id) === null) continue;
    if (["first_name", "last_name", "email", "phone", "resume", "cover_letter"].includes(id)) {
      continue; // already handled as a core field
    }

    const el = document.getElementById(id);
    const required = /\*/.test(label.textContent);
    const text = label.textContent.toLowerCase();

    if (text.includes("linkedin")) {
      if (fields.linkedin_url && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) {
        setNativeValue(el, fields.linkedin_url);
        verifyText(el, fields.linkedin_url, filled, holds, id);
        continue;
      }
      if (!required) continue;
    }

    if (required) {
      hold(el, holds, id, `unmapped custom question: "${label.textContent.replace("*", "").trim()}"`);
    }
  }
}

function verifyText(el, expected, filled, holds, id) {
  if (el.value === expected) {
    markFilled(el);
    filled.push(id);
  } else {
    hold(el, holds, id, "did not verify after fill");
  }
}

function hold(el, holds, id, reason) {
  if (el) markHold(el);
  holds.push({ field: id, reason });
}

function markFilled(el) {
  el.style.outline = "2px solid #16a34a";
}

function markHold(el) {
  el.style.outline = "2px solid #d97706";
  el.style.backgroundColor = "#fffbeb";
}

// React controls the DOM here, so a plain `el.value = x` is invisible to it —
// the framework's own setter has to be called, then an `input` event dispatched
// so React's onChange fires and its state catches up with the DOM.
function setNativeValue(el, value) {
  const proto = el.tagName === "TEXTAREA" ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function setFile(el, file) {
  const dt = new DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function dataUrlToFile(dataUrl, filename) {
  const res = await fetch(dataUrl);
  const blob = await res.blob();
  return new File([blob], filename, { type: "application/pdf" });
}
