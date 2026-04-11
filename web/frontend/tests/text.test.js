import test from 'node:test';
import assert from 'node:assert/strict';

import { compareTextScores, repairMojibakeText } from '../src/utils/text.ts';

test('compareTextScores uses numeric tuple ordering', () => {
  assert.equal(compareTextScores([10, 0, 0, 0], [2, 0, 0, 0]) > 0, true);
  assert.equal(compareTextScores([2, -1, 0, 0], [2, -3, 0, 0]) > 0, true);
  assert.equal(compareTextScores([2, -1, 0, 0], [2, -1, 0, 0]), 0);
});

test('repairMojibakeText repairs common UTF-8 decoded as Latin-1 mojibake', () => {
  assert.equal(repairMojibakeText('ä¸­æ'), '中文');
  assert.equal(repairMojibakeText('æµè¯æä»¶å'), '测试文件名');
});
