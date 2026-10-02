const test = require('node:test');
const assert = require('node:assert');
const SE = require('./solution_engine.js');

// 工具：安全取 essence 列表（兼容数组或对象形式）
function essenceList() {
  const e = SE.ESSENCES;
  return Array.isArray(e) ? e : Object.keys(e);
}

// 工具：构造一个伪方案
function fakeSol(essence) {
  return { essence, title: 't-' + essence };
}

test('1) ESSENCES 长度 >= 16 且所有 key 全局唯一', () => {
  const list = essenceList();
  assert.ok(list.length >= 16, 'ESSENCES 长度应 >= 16，实际: ' + list.length);
  const set = new Set(list);
  assert.equal(set.size, list.length, 'ESSENCES 存在重复 key');
});

test('2) buildSolutions 返回 5 个方案，essence 唯一，末位 TRANSFORM', () => {
  const sols = SE.buildSolutions(
    { bug_type: 'command_injection', language: 'python', victim: 'os.system' },
    5
  );
  assert.ok(Array.isArray(sols));
  assert.equal(sols.length, 5);
  const ess = sols.map(s => s.essence);
  assert.equal(new Set(ess).size, 5, 'essence 应全部唯一');
  assert.equal(ess[4], 'TRANSFORM', '最后一个方案 essence 应为 TRANSFORM');
});

test('3) 未知 bug_type 不崩，仍返回 3 个以上方案', () => {
  const sols = SE.buildSolutions(
    { bug_type: 'whatever', language: 'python', victim: 'x' },
    5
  );
  assert.ok(Array.isArray(sols));
  assert.ok(sols.length > 3, '未知 bug_type 也应返回 3 个以上方案');
});

test('4) --count 边界：count=2 至少 3 个；count=99 不超过 7 个', () => {
  const low = SE.buildSolutions(
    { bug_type: 'command_injection', language: 'python', victim: 'os.system' },
    2
  );
  assert.ok(low.length >= 3, 'count=2 时应至少返回 3 个方案');

  const high = SE.buildSolutions(
    { bug_type: 'command_injection', language: 'python', victim: 'os.system' },
    99
  );
  assert.ok(high.length <= 7, 'count=99 时应不超过 7 个方案');
});

test('5) distinctCheck：重复 essence 报告 ok=false；全唯一 ok=true', () => {
  const dup = [fakeSol('A'), fakeSol('A'), fakeSol('B')];
  const r1 = SE.distinctCheck(dup);
  assert.equal(r1.ok, false, '相同 essence 应判定为不通过');
  const dumped = JSON.stringify(r1);
  assert.ok(dumped.indexOf('A') !== -1, '报告中应指出重复的 essence');

  const uniq = [fakeSol('A'), fakeSol('B'), fakeSol('C')];
  const r2 = SE.distinctCheck(uniq);
  assert.equal(r2.ok, true, '全唯一 essence 应判定为通过');
});

test('6) expandForTot：含 node/essence/children，children=5 且 aspect 依次固定', () => {
  const sol = SE.buildSolutions(
    { bug_type: 'command_injection', language: 'python', victim: 'os.system' },
    3
  )[0];
  const t = SE.expandForTot(sol);
  assert.ok(t && typeof t === 'object');
  assert.ok('node' in t, '返回结构应含 node');
  assert.ok('essence' in t, '返回结构应含 essence');
  assert.ok(Array.isArray(t.children), '返回结构应含 children 数组');
  assert.equal(t.children.length, 5);
  const aspects = t.children.map(c => c.aspect);
  assert.deepEqual(
    aspects,
    ['优点', '缺点', '影响范围', '兼容性', '实现']
  );
});

test('7) classifyLevel：L1/L2/L3 及 evidence 提升', () => {
  const one = [fakeSol('A')];
  const two = [fakeSol('A'), fakeSol('B')];
  const three = [fakeSol('A'), fakeSol('B'), fakeSol('C')];

  assert.equal(SE.classifyLevel(one, {}).level, 'L1');
  assert.equal(SE.classifyLevel(two, {}).level, 'L2');
  assert.equal(SE.classifyLevel(three, {}).level, 'L3');

  // evidence.ran=true → 不低于 L3
  const ranLevel = SE.classifyLevel(one, { ran: true });
  assert.ok(
    ranLevel.level === 'L3' || ranLevel.level === 'L4',
    'ran=true 时等级应不低于 L3，实际: ' + ranLevel.level
  );

  // evidence.improved=true → L4
  const impLevel = SE.classifyLevel(one, { ran: true, improved: true });
  assert.equal(impLevel.level, 'L4');
});

test('8) language 字段 autofill：analysis.language 提供时透传到每个方案', () => {
  const sols = SE.buildSolutions(
    { bug_type: 'command_injection', language: 'python', victim: 'os.system' },
    5
  );
  sols.forEach((s, i) => {
    assert.equal(s.language, 'python', '方案 ' + i + ' 应 autofill language');
  });
});