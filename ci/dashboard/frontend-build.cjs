'use strict';

// Build-time adapter for the pinned upstream bundle; source drift stops the build.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const SOURCE_SHA256 = '6d3accdd96d0183ad2d3b2770c43fb214147b545a5a5db30e5accdbafa74d8ab';
const digest = value => crypto.createHash('sha256').update(value).digest('hex');

function build(source) {
  const actualHash = digest(source);
  if (actualHash !== SOURCE_SHA256) throw new Error('source SHA256 mismatch: ' + actualHash);
  return transform(source);
}
function transform(source) {
  const actualHash = digest(source);
  if (source.includes('s20pFlowMeta') || source.includes('s20pDnsStatus')) {
    throw new Error('candidate identifiers already exist in source');
  }
  const transforms = [];
  function replaceExact(text, before, after, expected, name) {
    const matches = text.split(before).length - 1;
    if (matches !== expected) {
      throw new Error(`${name}: expected ${expected} matches, received ${matches}`);
    }
    transforms.push({ name, expected, matches });
    return text.split(before).join(after);
  }

  const componentStart = source.indexOf('F5=I({setup(o)');
  const componentEnd = source.indexOf(';var Fa=N(F5,', componentStart);
  if (componentStart < 0 || componentEnd <= componentStart) {
    throw new Error('traffic component boundaries not found');
  }
  let traffic = source.slice(componentStart, componentEnd);
  traffic = replaceExact(
    traffic,
    'const a=E(),l=',
    'const a=E(),s20pFlowMeta=E(null),s20pFlowLabel=(s20pLegacy,s20pKind)=>{if(s20pFlowMeta.value&&s20pFlowMeta.value.measurement==="interface-rx-tx"){if(s20pKind==="title")return "介面流量（"+s20pFlowMeta.value.device+"）";return s20pKind==="rx"?"RX 接收":"TX 發送"}return n(s20pLegacy)},l=',
    1,
    'traffic-metadata-and-label-helper',
  );

  const legacySamples = 'const C=x.data.result.slots||10;if(x.data.result.items.length<C){let S=x.data.result.items;for(;S.length<C;)S=[{downloadSpeed:0,endTime:0,startTime:0,uploadSpeed:0}].concat(S);a.value=S}else x.data.result.items.length>C?a.value=x.data.result.items.slice(C-x.data.result.items.length):a.value=x.data.result.items';
  traffic = replaceExact(
    traffic,
    legacySamples,
    's20pFlowMeta.value=x.data.result.dashboardAdapter||null;if(s20pFlowMeta.value)a.value=x.data.result.items.slice(-12);else{' + legacySamples + '}',
    1,
    'adapter-real-sample-history',
  );

  const labels = [
    ['n("\\u4E0B\\u8F7D")', 's20pFlowLabel("\\u4E0B\\u8F7D","rx")', 2, 'chart-rx-series'],
    ['n("\\u4E0A\\u4F20")', 's20pFlowLabel("\\u4E0A\\u4F20","tx")', 2, 'chart-tx-series'],
    ['e(n)("\\u4E0B\\u8F7D")', 's20pFlowLabel("\\u4E0B\\u8F7D","rx")', 1, 'legend-rx'],
    ['e(n)("\\u4E0A\\u4F20")', 's20pFlowLabel("\\u4E0A\\u4F20","tx")', 1, 'legend-tx'],
    ['e(n)("\\u4E0B\\u8F7D\\u901F\\u5EA6")', 's20pFlowLabel("\\u4E0B\\u8F7D\\u901F\\u5EA6","rx")', 1, 'speed-rx'],
    ['e(n)("\\u4E0A\\u4F20\\u901F\\u5EA6")', 's20pFlowLabel("\\u4E0A\\u4F20\\u901F\\u5EA6","tx")', 1, 'speed-tx'],
    ['e(n)("\\u7F51\\u7EDC\\u6D41\\u91CF")', 's20pFlowLabel("\\u7F51\\u7EDC\\u6D41\\u91CF","title")', 1, 'interface-traffic-title'],
  ];
  for (const [before, after, expected, name] of labels) {
    traffic = replaceExact(traffic, before, after, expected, name);
  }
  let code = source.slice(0, componentStart) + traffic + source.slice(componentEnd);
  const dnsAnchor = 'const Ka=()=>{const o=document.createElement("div");document.body.appendChild(o);const n=xt(_3,{Close:()=>{a()}});n.mount(o);const a=()=>{n.unmount(),o.remove()};return{Close:a}},x3=["onSubmit"]';
  const dnsGuard = 'const s20pDnsStatus=Fe().status;if(s20pDnsStatus&&s20pDnsStatus.dashboardAdapter&&s20pDnsStatus.dashboardAdapter.dnsReadOnly===true){window.alert("旁路由 DNS 狀態僅供檢視，請至「網路 → 介面 → LAN」修改；此按鈕不會套用 WAN DNS 設定。");return}';
  code = replaceExact(code, dnsAnchor, dnsAnchor.replace('const Ka=()=>{', 'const Ka=()=>{' + dnsGuard), 1, 'dns-read-only-entry-guard');

  return {
    code,
    manifest: {
      purpose: 'Local QuickStart frontend candidate for explicit interface-RX/TX adapter metadata',
      source: { file: 'quickstart-installed-index.js', sha256: actualHash, bytes: Buffer.byteLength(source) },
      output: { file: 'frontend-index.js', sha256: digest(code), bytes: Buffer.byteLength(code) },
      transforms,
    },
  };
}

module.exports = { build, transform, SOURCE_SHA256 };

if (require.main === module) {
  const sourcePath = process.argv[2] || path.join(__dirname, 'missing-input.js');
  const outputDir = process.argv[3] || __dirname;
  const result = build(fs.readFileSync(sourcePath, 'utf8'));
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(path.join(outputDir, 'frontend-index.js'), result.code);
  fs.writeFileSync(path.join(outputDir, 'frontend-manifest.json'), JSON.stringify(result.manifest, null, 2) + '\n');
  process.stdout.write(JSON.stringify(result.manifest, null, 2) + '\n');
}
