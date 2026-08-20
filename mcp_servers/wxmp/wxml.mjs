/**
 * Minimal WXML reader for page snapshots.
 *
 * The rendered WXML of a single page runs to several KB, most of it CSS-module
 * class names and layout wrappers. Handing that to a model is the mini program
 * equivalent of dumping raw DOM, which Hercules avoids for browsers because
 * large payloads trip provider-side token limits. So we parse locally and emit
 * only what a tester would look at: visible text and interactive controls,
 * each with the element id automator can act on.
 */

const VOID_TAGS = new Set(["image", "input", "icon", "progress", "br"]);

// Controls a user can act on, even when they carry no text of their own.
const INTERACTIVE = new Set([
  "button", "input", "textarea", "switch", "slider", "checkbox",
  "radio", "picker", "video", "audio", "web-view", "navigator",
]);

function parseAttrs(raw) {
  const attrs = {};
  const re = /([a-zA-Z_:][\w:.-]*)\s*=\s*"([^"]*)"/g;
  let m;
  while ((m = re.exec(raw))) attrs[m[1]] = m[2];
  return attrs;
}

/** Flatten rendered WXML into nodes carrying text or interactivity. */
export function parseWxml(wxml) {
  const nodes = [];
  const stack = [];
  const re = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|[^>])*?)(\/?)>|([^<]+)/g;
  let m;

  while ((m = re.exec(wxml))) {
    const [, closing, tag, rawAttrs, selfClose, textChunk] = m;

    if (textChunk !== undefined) {
      const text = textChunk.replace(/\s+/g, " ").trim();
      if (text && stack.length) stack[stack.length - 1].text.push(text);
      continue;
    }
    if (closing) {
      const node = stack.pop();
      if (node) finish(node, nodes);
      continue;
    }
    const attrs = parseAttrs(rawAttrs || "");
    const node = { tag, attrs, text: [] };
    if (selfClose || VOID_TAGS.has(tag)) finish(node, nodes);
    else stack.push(node);
  }
  while (stack.length) finish(stack.pop(), nodes);
  return nodes;
}

function finish(node, nodes) {
  const text = node.text.join(" ").replace(/\s+/g, " ").trim();
  const interactive = INTERACTIVE.has(node.tag);
  if (!text && !interactive) return;
  nodes.push({
    tag: node.tag,
    id: node.attrs.id || node.attrs["data-sid"] || null,
    testId: node.attrs["data-testid"] || node.attrs["data-test-id"] || null,
    text,
    disabled: node.attrs.disabled !== undefined,
    checked: node.attrs.checked !== undefined,
    src: node.attrs.src || null,
    // Inputs rarely carry text of their own; the placeholder is what a person
    // reads to know which field is which, so it is how we address them.
    placeholder: node.attrs.placeholder || null,
    value: node.attrs.value || null,
  });
}

/** Compact, model-facing rendering of a page. */
export function formatSnapshot(nodes) {
  if (!nodes.length) return "(page has no visible text or controls)";
  const seen = new Set();
  const lines = [];
  for (const n of nodes) {
    const key = `${n.tag}|${n.text}|${n.id}|${n.placeholder}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const bits = [];
    if (n.id) bits.push(`#${n.id}`);
    if (n.testId) bits.push(`testId=${n.testId}`);
    if (n.disabled) bits.push("disabled");
    if (n.checked) bits.push("checked");
    if (n.placeholder) bits.push(`placeholder="${n.placeholder}"`);
    if (n.value) bits.push(`value="${n.value}"`);
    if (n.src) bits.push(`src=${n.src.slice(0, 60)}`);
    const suffix = bits.length ? `  [${bits.join(" ")}]` : "";
    lines.push(`${n.tag}${n.text ? ` "${n.text}"` : ""}${suffix}`);
  }
  return lines.join("\n");
}

/** Elements whose text matches, most specific (shortest text) first. */
export function findByText(nodes, needle, exact = false) {
  const want = needle.trim();
  return nodes
    .filter((n) => n.id && n.text && (exact ? n.text === want : n.text.includes(want)))
    .sort((a, b) => a.text.length - b.text.length);
}

const INPUT_TAGS = new Set(["input", "textarea"]);

/** Input-like elements, optionally narrowed by placeholder or nearby label. */
export function findInputs(nodes, near = "") {
  const inputs = nodes.filter((n) => n.id && INPUT_TAGS.has(n.tag));
  if (!near) return inputs;
  const want = near.trim();
  const byPlaceholder = inputs.filter((n) => (n.placeholder || "").includes(want));
  if (byPlaceholder.length) return byPlaceholder;
  // Fall back to document order: the input that follows the label text.
  const labelAt = nodes.findIndex((n) => n.text && n.text.includes(want));
  if (labelAt === -1) return [];
  const after = nodes.slice(labelAt).find((n) => n.id && INPUT_TAGS.has(n.tag));
  return after ? [after] : [];
}
