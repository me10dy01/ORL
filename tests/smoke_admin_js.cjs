// 前端冒烟测试（管理页）：在最小 DOM stub 中执行 admin.js 顶层初始化，并严格校验选择器。
//
// 与 smoke_app_js.cjs 同款防回归机制（校验规则）：
//   1. admin.js 的元素查找应全部是 "#<id>" 形式，且该 id 必须真实存在于 app/static/admin.html；
//   2. 否则记入无效选择器，最终以非 0 退出。
// 背景：app.js 曾因漏写 "#" 导致整站脚本失效，此测试将该类问题拦在提交前。
//
// 用法：node tests/smoke_admin_js.cjs   （退出码 0 = 初始化无异常且选择器全部有效）

const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");

function makeEl() {
  return new Proxy(() => {}, {
    get(_t, prop) {
      if (prop === "classList") {
        return { add() {}, remove() {}, toggle() {}, contains: () => false };
      }
      if (prop === "style" || prop === "dataset") return {};
      if (prop === "files") return [];
      if (prop === "value") return "";
      if (prop === "textContent") return "";
      return makeEl();
    },
    set() {
      return true;
    },
    apply() {
      return makeEl();
    },
  });
}

const html = fs.readFileSync(
  path.join(root, "app", "static", "admin.html"),
  "utf8",
);
const knownIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

const invalidSelectors = [];

global.Node = function Node() {};
global.window = { confirm: () => true };
global.location = { protocol: "http:" };
global.document = {
  querySelector(selector) {
    if (typeof selector === "string" && selector.startsWith("#")) {
      const id = selector.slice(1).split(/[\s>]/)[0];
      if (knownIds.has(id)) return makeEl();
      invalidSelectors.push(`${selector}（页面中不存在该 id）`);
      return null;
    }
    invalidSelectors.push(`${selector}（应为 #id 选择器）`);
    return null; // 与真实浏览器一致：匹配不到就是 null
  },
  createElement: () => makeEl(),
  createTextNode: () => makeEl(),
  addEventListener: () => {},
  body: makeEl(),
};
global.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => [],
  body: null,
});

process.on("unhandledRejection", (err) => {
  process.stderr.write(`未处理的 Promise 异常: ${err}\n`);
  process.exit(1);
});

try {
  require(path.join(root, "app", "static", "admin.js"));
} catch (err) {
  process.stderr.write(`admin.js 初始化抛异常: ${err}\n`);
  process.exit(1);
}

if (invalidSelectors.length > 0) {
  process.stderr.write(
    `发现 ${invalidSelectors.length} 个无效选择器:\n  ${invalidSelectors.join("\n  ")}\n`,
  );
  process.exit(1);
}

process.stdout.write(
  `admin.js 初始化正常，选择器全部有效（已校验 ${knownIds.size} 个页面 id）\n`,
);
