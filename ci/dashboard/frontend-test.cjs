'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const sourcePath = path.join(__dirname, 'tests', 'frontend-fixture.js');
const builderPath = path.join(__dirname, 'frontend-build.cjs');
const source = fs.readFileSync(sourcePath, 'utf8');

function candidate() {
  assert.ok(fs.existsSync(builderPath), 'frontend candidate generator must exist');
  return require(builderPath).transform(source);
}

function dnsContext(status) {
  const events = [];
  const { code } = candidate();
  const begin = code.indexOf('const Ka=()=>{');
  const end = code.indexOf(',x3=["onSubmit"]', begin);
  assert.ok(begin >= 0 && end > begin, 'DNS entry must remain uniquely extractable');
  const context = {
    Fe: () => ({ status }),
    window: { alert: value => events.push(['alert', value]) },
    document: {
      createElement: () => { events.push(['create']); return { remove: () => events.push(['remove']) }; },
      body: { appendChild: () => events.push(['append']) },
    },
    xt: () => ({ mount: () => events.push(['mount']), unmount: () => events.push(['unmount']) }),
    _3: {},
  };
  vm.runInNewContext(code.slice(begin, end) + '; globalThis.openDns = Ka;', context);
  return { events, result: context.openDns() };
}

function trafficContext() {
  const { code } = candidate();
  const begin = code.indexOf('F5=I({setup(o)');
  const end = code.indexOf(';var Fa=N(F5,', begin);
  const component = code.slice(begin, end);
  const setupBegin = component.indexOf('const a=E(),');
  const setupEnd = component.indexOf(',l=', setupBegin);
  const loaderBegin = component.indexOf('const g=()=>');
  const loaderEnd = component.indexOf(',p=Mt.dateForm', loaderBegin);
  assert.ok(setupBegin >= 0 && setupEnd > setupBegin && loaderEnd > loaderBegin);
  const context = {
    E: value => ({ value }),
    n: value => `translated:${value}`,
    nextResponse: null,
    G: { Network: { Statistics: { GET: () => context.nextResponse } } },
    L: (_self, _args, body) => {
      const generator = body();
      let step = generator.next();
      while (!step.done) step = generator.next(step.value);
      return step.value;
    },
    console: { log: err => { throw err; } },
  };
  vm.runInNewContext(
    component.slice(setupBegin, setupEnd) + ';' + component.slice(loaderBegin, loaderEnd) +
      '; globalThis.readFlow = g; globalThis.samples = a; globalThis.label = s20pFlowLabel;',
    context,
  );
  const load = (items, metadata, slots) => {
    context.nextResponse = { data: { result: { items, slots, ...(metadata ? { dashboardAdapter: metadata } : {}) } } };
    context.readFlow();
    return context.samples.value;
  };
  return { load, label: context.label, component };
}

const metadata = { device: 'br-lan', measurement: 'interface-rx-tx', dnsReadOnly: true };

test('build uses a pinned source with bounded transformations and checksums', () => {
  const result = candidate();
  assert.match(result.manifest.source.sha256, /^[a-f0-9]{64}$/);
  assert.match(result.manifest.output.sha256, /^[a-f0-9]{64}$/);
  assert.equal(result.manifest.transforms.reduce((sum, item) => sum + item.matches, 0), 12);
  assert.equal(result.manifest.transforms.length, 10);
  assert.throws(() => require(builderPath).build(source + ' '), /source SHA256 mismatch/);
});

test('DNS read-only adapter blocks the WAN modal before any DOM operation', () => {
  const { events, result } = dnsContext({ dashboardAdapter: metadata });
  assert.equal(result, undefined);
  assert.deepEqual(events.map(event => event[0]), ['alert']);
  assert.match(events[0][1], /LAN/);
});

test('DNS legacy backend still opens and closes its original modal', () => {
  const { events, result } = dnsContext({});
  assert.deepEqual(events.map(event => event[0]), ['create', 'append', 'mount']);
  result.Close();
  assert.deepEqual(events.map(event => event[0]), ['create', 'append', 'mount', 'unmount', 'remove']);
});

test('DNS explicit false does not block a normal writable backend', () => {
  const { events } = dnsContext({ dashboardAdapter: { dnsReadOnly: false } });
  assert.equal(events.some(event => event[0] === 'mount'), true);
  assert.equal(events.some(event => event[0] === 'alert'), false);
});

test('adapter keeps one real sample without invented zero history', () => {
  const { load, label } = trafficContext();
  const item = { downloadSpeed: 9, uploadSpeed: 4, startTime: 10, endTime: 11 };
  const samples = load([item], metadata, 10);
  assert.equal(samples.length, 1);
  assert.equal(samples[0], item);
  assert.equal(label('網絡流量', 'title'), '介面流量（br-lan）');
  assert.equal(label('下載', 'rx'), 'RX 接收');
  assert.equal(label('上傳', 'tx'), 'TX 發送');
});

test('adapter retains last twelve actual samples and supports an empty history', () => {
  const { load } = trafficContext();
  const items = Array.from({ length: 20 }, (_, index) => ({ startTime: index + 1 }));
  const samples = load(items, metadata, 999);
  assert.equal(samples.length, 12);
  assert.equal(samples[0], items[8]);
  assert.equal(samples[11], items[19]);
  assert.equal(load([], metadata, 10).length, 0);
});

test('legacy statistics keep original padding and translated labels', () => {
  const { load, label } = trafficContext();
  const item = { downloadSpeed: 9, uploadSpeed: 4, startTime: 10, endTime: 11 };
  const samples = load([item], null, 10);
  assert.equal(samples.length, 10);
  assert.equal(samples[0].startTime, 0);
  assert.equal(samples[9], item);
  assert.equal(label('網絡流量', 'title'), 'translated:網絡流量');
  assert.equal(label('下載', 'rx'), 'translated:下載');
});

test('adapter metadata is reset when a later response comes from a normal backend', () => {
  const { load, label } = trafficContext();
  load([{ startTime: 1 }], metadata, 10);
  assert.equal(label('下載', 'rx'), 'RX 接收');
  load([{ startTime: 2 }], null, 3);
  assert.equal(label('下載', 'rx'), 'translated:下載');
});

test('all chart, legend and speed labels use metadata-gated RX/TX wording', () => {
  const { component } = trafficContext();
  assert.equal((component.match(/s20pFlowLabel\([^)]*,"rx"\)/g) || []).length, 4);
  assert.equal((component.match(/s20pFlowLabel\([^)]*,"tx"\)/g) || []).length, 4);
  assert.equal((component.match(/s20pFlowLabel\([^)]*,"title"\)/g) || []).length, 1);
});
