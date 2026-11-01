import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import { buildSync } from 'esbuild';
import { polygonError, fenceGeometrySchema, fenceStateSchema } from './FenceEditor.tsx';

const point = (north_m, east_m) => ({ north_m, east_m });
const inclusion = [point(-100, -100), point(-100, 100), point(100, 100), point(100, -100)];
const initial = {
  version: 0,
  names: { inclusion: 'Inclusion', exclusions: [] },
  inclusion,
  exclusions: [],
  editable: true,
  verified: true,
  error: null,
};
const code = buildSync({
  entryPoints: [new URL('./FenceEditor.tsx', import.meta.url).pathname],
  bundle: true,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  write: false,
}).outputFiles[0].text;

function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  return node && typeof node === 'object' && node.props ? [node, ...nodes(node.props.children)] : [];
}
function label(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(label).join('');
  return node?.props ? label(node.props.children) : '';
}
function editor() {
  const values = [],
    refs = [],
    effects = [];
  let si, ri, ei, dirty, tree;
  const sent = [];
  const props = {
    fences: initial,
    connected: true,
    result: null,
    marking: false,
    onEditing() {},
    save: (fences) => {
      sent.push(fences);
      return true;
    },
    project: (north, east) => ({ x: north, y: east }),
    unproject: point,
    size: { width: 800, height: 600 },
  };
  const require = createRequire(import.meta.url);
  const module = { exports: {} };
  const pending = [];
  runInNewContext(code, {
    module,
    exports: module.exports,
    require: (name) =>
      name !== 'react'
        ? require(name)
        : {
            ...require('react'),
            useState(initial) {
              const index = si++;
              if (!(index in values)) values[index] = initial;
              return [
                values[index],
                (value) => {
                  values[index] = typeof value === 'function' ? value(values[index]) : value;
                  dirty = true;
                },
              ];
            },
            useRef(initial) {
              return (refs[ri++] ??= { current: initial });
            },
            useEffect(callback, dependencies) {
              const index = ei++;
              if (effects[index] && dependencies.every((v, i) => Object.is(v, effects[index][i]))) return;
              effects[index] = dependencies;
              pending.push(callback);
            },
          },
  });
  function render(updates = {}) {
    Object.assign(props, updates);
    let count = 0;
    do {
      assert(count++ < 20);
      dirty = false;
      si = ri = ei = 0;
      tree = module.exports.default(props);
      for (const node of nodes(tree)) {
        if (node.type === 'dialog') node.props.ref.current = { showModal() {} };
      }
      for (const effect of pending.splice(0)) effect();
    } while (dirty);
  }
  render();
  function find(predicate) {
    const node = nodes(tree).find(predicate);
    assert(node);
    return node;
  }
  const button = (name) =>
    find((n) => n.type === 'button' && (n.props['aria-label'] || label(n.props.children)) === name);
  const pointer = (north, east) => ({
    button: 0,
    clientX: north,
    clientY: east,
    pointerId: 1,
    stopPropagation() {},
    currentTarget: { setPointerCapture() {} },
  });
  return {
    sent,
    render,
    button,
    find,
    pointer,
    click(name) {
      button(name).props.onClick();
      render();
    },
    draw(north, east) {
      find((n) => n.props.className === 'gcs-fence-draw-target').props.onPointerUp(pointer(north, east));
      render();
    },
    close() {
      find((n) => n.props['data-fence-corner'] === 0).props.onPointerDown(pointer(0, 0));
      render();
    },
    name(value) {
      find((node) => node.type === 'input').props.onChange({ target: { value } });
      render();
    },
    submit() {
      assert.equal(button('Save area').props.disabled, false);
      find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
      render();
    },
    text: () => label(tree),
  };
}

test('fence protocol carries separate inclusion and exclusion polygons', () => {
  assert(fenceStateSchema.safeParse(initial).success);
  assert(fenceGeometrySchema.safeParse({ inclusion, exclusions: [inclusion] }).success);
  assert(!fenceGeometrySchema.safeParse(inclusion).success);
  assert(!fenceGeometrySchema.safeParse({ inclusion: [point(0, 0), point(0, 1)], exclusions: [] }).success);
  for (const names of [
    { inclusion: '', exclusions: [] },
    { inclusion: 'Inclusion', exclusions: ['Orphan name'] },
    { inclusion: 'x'.repeat(61), exclusions: [] },
    { inclusion: ' Untrimmed ', exclusions: [] },
  ])
    assert(!fenceStateSchema.safeParse({ ...initial, names }).success);
});

test('lines, repeated corners and crossings cannot become fence areas', () => {
  assert.equal(polygonError(inclusion), null);
  for (const shape of [
    [point(0, 0), point(1, 1)],
    [point(0, 0), point(1, 1), point(2, 2)],
    [...inclusion, inclusion[0]],
    [point(0, 0), point(10, 10), point(0, 10), point(10, 0)],
    [point(0, 0), point(NaN, 1), point(1, 0)],
  ])
    assert.notEqual(polygonError(shape), null);
});

test('draw requires an explicit close before save and sends one canonical fence configuration', () => {
  const e = editor();
  e.click('Exclusion');
  e.draw(0, 0);
  e.draw(0, 20);
  assert(e.button('Save fences').props.disabled);
  e.draw(20, 20);
  e.draw(20, 0);
  assert(e.button('Save fences').props.disabled);
  e.close();
  assert.equal(e.button('Save fences').props.disabled, false);
  e.click('Save fences');
  assert.equal(e.sent.length, 0);
  assert.equal(e.find((node) => node.type === 'input').props.value, 'Exclusion 1');
  e.name('Bear habitat');
  e.submit();
  assert.equal(e.sent.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(e.sent[0])), {
    version: 0,
    names: { inclusion: 'Inclusion', exclusions: ['Bear habitat'] },
    inclusion,
    exclusions: [[point(0, 0), point(0, 20), point(20, 20), point(20, 0)]],
  });
  e.render({ result: { type: 'fence_result', version: 1, error: null }, fences: { ...initial, version: 1 } });
  assert(!e.text().includes('Save fences'));
});

test('closed lines stay invalid and undo restores the previous corner', () => {
  const e = editor();
  e.click('Exclusion');
  e.draw(0, 0);
  e.draw(10, 10);
  e.draw(20, 20);
  e.close();
  assert(e.button('Save fences').props.disabled);
  assert.match(e.text(), /line is not an area/);
  e.click('Undo fence edit');
  assert(e.button('Save fences').props.disabled);
  e.click('Undo fence edit');
  e.draw(0, 20);
  e.close();
  assert.equal(e.button('Save fences').props.disabled, false);
});

test('dragging an inclusion corner is one undoable change', () => {
  const e = editor();
  e.click('Inclusion');
  const corner = () => e.find((node) => node.props['data-fence-corner'] === 0);
  corner().props.onPointerDown(e.pointer(-100, -100));
  e.render();
  corner().props.onPointerMove(e.pointer(-120, -100));
  e.render();
  corner().props.onPointerMove(e.pointer(-130, -100));
  e.render();
  corner().props.onPointerUp(e.pointer(-130, -100));
  e.render();
  assert.equal(e.button('Save fences').props.disabled, false);
  e.click('Save fences');
  assert.equal(e.button('Save area').props.disabled, false);
  e.click('Back to editing');
  e.click('Undo fence edit');
  e.click('Save fences');
  assert(e.button('Save area').props.disabled);
});

test('deleting and undoing an exclusion keep the other polygon names associated', () => {
  const e = editor();
  const first = [point(0, 0), point(0, 20), point(20, 0)];
  const second = [point(30, 30), point(30, 40), point(40, 30)];
  e.render({
    fences: {
      ...initial,
      names: { inclusion: 'Park', exclusions: ['Bear habitat', 'Campground'] },
      exclusions: [first, second],
    },
  });
  e.click('Exclusion');
  e.click('Bear habitat');
  e.click('Delete zone');
  assert.equal(e.sent.length, 0);
  assert.equal(e.button('Save fences').props.disabled, false);
  e.click('Undo fence edit');
  e.click('Save fences');
  assert.equal(e.find((node) => node.type === 'input').props.value, 'Bear habitat');
  assert(e.button('Save area').props.disabled);
  e.click('Back to editing');
  e.click('Delete zone');
  e.click('Save fences');
  assert.deepEqual(JSON.parse(JSON.stringify(e.sent[0])), {
    version: 0,
    names: { inclusion: 'Park', exclusions: ['Campground'] },
    inclusion,
    exclusions: [second],
  });
});

test('airborne and stale edits cannot save; exact server errors stay in the editor', () => {
  const e = editor();
  e.click('Exclusion');
  e.draw(0, 0);
  e.draw(0, 20);
  e.draw(20, 0);
  e.close();
  e.render({ fences: { ...initial, editable: false } });
  assert(e.button('Save fences').props.disabled);
  e.render({ fences: { ...initial, version: 1 } });
  assert(e.button('Save fences').props.disabled);
  e.render({ fences: initial });
  e.click('Save fences');
  e.submit();
  e.render({ result: { type: 'fence_result', version: 0, error: 'Drone2 home is inside Exclusion 1' } });
  assert.match(e.text(), /Drone2 home is inside Exclusion 1/);
  assert.equal(e.sent.length, 1);
});

test('name dialog can cancel and rename inclusion without moving its corners', () => {
  const e = editor();
  e.click('Inclusion');
  e.click('Save fences');
  assert(e.button('Save area').props.disabled);
  e.name('Park boundary');
  e.click('Back to editing');
  assert.equal(e.sent.length, 0);
  e.click('Save fences');
  assert.equal(e.find((node) => node.type === 'input').props.value, 'Inclusion');
  e.name('   ');
  assert(e.button('Save area').props.disabled);
  e.name('x'.repeat(61));
  assert(e.button('Save area').props.disabled);
  e.name('  Park boundary  ');
  e.submit();
  assert.deepEqual(JSON.parse(JSON.stringify(e.sent[0])), {
    version: 0,
    names: { inclusion: 'Park boundary', exclusions: [] },
    inclusion,
    exclusions: [],
  });
});

test('a stale or airborne state during naming blocks submission', () => {
  const e = editor();
  e.click('Inclusion');
  e.click('Save fences');
  e.name('Park boundary');
  for (const fences of [
    { ...initial, version: 1 },
    { ...initial, editable: false },
  ]) {
    e.render({ fences });
    assert(e.button('Save area').props.disabled);
    e.find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
    e.render();
    assert.equal(e.sent.length, 0);
  }
});
