import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Run the real component handlers without a DOM or another test dependency.
const compiled = ts.transpileModule(
  readFileSync(new URL("../src/pages.tsx", import.meta.url), "utf8"),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
).outputText;

function mount(api) {
  const state = [];
  let cursor = 0;
  const useState = (initial) => {
    const i = cursor++;
    if (!(i in state)) state[i] = initial;
    return [
      state[i],
      (value) => {
        state[i] = value;
      },
    ];
  };
  const element = (type, props) => ({ type, props });
  const modules = {
    react: {
      useState,
      useRef: (current) => useState({ current })[0],
      useContext: () => ({ reload() {} }),
    },
    "react/jsx-runtime": { jsx: element, jsxs: element },
    "./api": { api },
    "./ui": {
      useAuth: () => ({ profile: { permissions: ["orders.write"] } }),
      useData: () => ({ data: [], loading: false, error: null }),
      useUI: () => ({ toast() {} }),
    },
  };
  const sandbox = {
    exports: {},
    require: (name) => modules[name] || {},
    FormData,
  };
  vm.runInNewContext(compiled, sandbox);
  return () => {
    cursor = 0;
    return sandbox.exports.ImportPage();
  };
}

function find(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}

const byId = (tree, id) => find(tree, (node) => node.props?.id === id);
const previewButton = (tree) =>
  find(
    tree,
    (node) => node.type === "button" && node.props.children === "开始预检",
  );
const confirmButton = (tree) =>
  find(
    tree,
    (node) =>
      node.type === "button" &&
      String(node.props.children).includes("确认导入"),
  );
const preview = {
  id: "preview-a",
  valid_count: 1,
  error_count: 0,
  errors: [],
  sample: [],
};
const chooseFile = (tree) =>
  byId(tree, "csv-file").props.onChange({
    target: { files: [new File(["external_id,product"], "orders.csv")] },
  });

test("changing the CSV or mapping invalidates its prior preview", async () => {
  const render = mount(async () => preview);
  chooseFile(render());
  await previewButton(render()).props.onClick();
  assert.ok(confirmButton(render()), "a successful preview can be confirmed");
  byId(render(), "mapping").props.onChange({
    target: { value: '{"订单号":"external_id"}' },
  });
  assert.equal(
    confirmButton(render()),
    null,
    "changed mappings need a new preview",
  );
  await previewButton(render()).props.onClick();
  assert.ok(confirmButton(render()));
  chooseFile(render());
  assert.equal(
    confirmButton(render()),
    null,
    "changed files need a new preview",
  );
});

test("preflight locks its inputs and a failed retry cannot confirm an old preview", async () => {
  let call = 0;
  let reject;
  const render = mount(() =>
    ++call === 1
      ? Promise.resolve(preview)
      : new Promise((_, fail) => {
          reject = fail;
        }),
  );
  chooseFile(render());
  await previewButton(render()).props.onClick();
  assert.ok(confirmButton(render()));
  const pending = previewButton(render()).props.onClick();
  assert.equal(byId(render(), "csv-file").props.disabled, true);
  assert.equal(byId(render(), "mapping").props.disabled, true);
  assert.equal(
    confirmButton(render()),
    null,
    "retry immediately retires the old preview",
  );
  reject(new Error("预检失败"));
  await pending;
  assert.equal(byId(render(), "csv-file").props.disabled, false);
  assert.equal(byId(render(), "mapping").props.disabled, false);
  assert.equal(confirmButton(render()), null);
  assert.ok(
    find(
      render(),
      (node) =>
        node.props?.role === "alert" && node.props.children === "预检失败",
    ),
  );
});
