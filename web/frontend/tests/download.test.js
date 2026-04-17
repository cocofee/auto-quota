import test from 'node:test';
import assert from 'node:assert/strict';

import api from '../src/services/api.ts';
import { downloadTaskResultExcel } from '../src/utils/download.ts';

function installDownloadDom() {
  let clicked = 0;
  let removed = 0;
  const createdUrls = [];
  const revokedUrls = [];
  const appended = [];

  const link = {
    href: '',
    setAttribute(name, value) {
      this[name] = value;
    },
    click() {
      clicked += 1;
    },
    remove() {
      removed += 1;
    },
  };

  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  globalThis.window = {
    URL: {
      createObjectURL(blob) {
        createdUrls.push(blob);
        return 'blob:test';
      },
      revokeObjectURL(url) {
        revokedUrls.push(url);
      },
    },
  };
  globalThis.document = {
    createElement(tag) {
      assert.equal(tag, 'a');
      return link;
    },
    body: {
      appendChild(node) {
        appended.push(node);
      },
    },
  };

  return {
    get clicked() {
      return clicked;
    },
    get removed() {
      return removed;
    },
    get createdUrls() {
      return createdUrls;
    },
    get revokedUrls() {
      return revokedUrls;
    },
    get appended() {
      return appended;
    },
    restore() {
      globalThis.window = previousWindow;
      globalThis.document = previousDocument;
    },
  };
}

test('downloadTaskResultExcel falls back to export only when export-final is unsupported', async () => {
  const dom = installDownloadDom();
  const calls = [];
  const originalGet = api.get;

  api.get = async (url, options) => {
    calls.push([url, options]);
    if (calls.length === 1) {
      throw { response: { status: 404 } };
    }
    return { data: new Blob(['ok']) };
  };

  try {
    await downloadTaskResultExcel('task-1', 'result.xlsx');
  } finally {
    api.get = originalGet;
    dom.restore();
  }

  assert.deepEqual(
    calls.map(([url]) => url),
    [
      '/tasks/task-1/export-final?materials=true',
      '/tasks/task-1/export?materials=true',
    ],
  );
  assert.equal(dom.clicked, 1);
  assert.equal(dom.removed, 1);
  assert.equal(dom.appended.length, 1);
  assert.deepEqual(dom.revokedUrls, ['blob:test']);
});

test('downloadTaskResultExcel surfaces export-final rebuild failures without falling back', async () => {
  const dom = installDownloadDom();
  const calls = [];
  const originalGet = api.get;

  api.get = async (url, options) => {
    calls.push([url, options]);
    throw {
      response: {
        status: 500,
        data: new Blob([JSON.stringify({ detail: 'final rebuild failed' })], { type: 'application/json' }),
      },
    };
  };

  try {
    await assert.rejects(
      () => downloadTaskResultExcel('task-2', 'result.xlsx'),
      /final rebuild failed/,
    );
  } finally {
    api.get = originalGet;
    dom.restore();
  }

  assert.deepEqual(
    calls.map(([url]) => url),
    ['/tasks/task-2/export-final?materials=true'],
  );
  assert.equal(dom.clicked, 0);
});
