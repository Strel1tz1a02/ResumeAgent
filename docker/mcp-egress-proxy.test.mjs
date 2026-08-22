import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { guardSocketPair, isPublicIp, resolvePublic } from './mcp-egress-proxy.mjs';

class FakeSocket extends EventEmitter {
  destroyed = false;
  destroyCalls = 0;

  destroy() {
    this.destroyCalls += 1;
    if (this.destroyed) return this;
    this.destroyed = true;
    this.emit('close');
    return this;
  }
}

test('allows public IPv4 and IPv6 addresses', () => {
  assert.equal(isPublicIp('8.8.8.8'), true);
  assert.equal(isPublicIp('2606:4700:4700::1111'), true);
  assert.equal(isPublicIp('::ffff:8.8.8.8'), true);
});

test('blocks non-public address ranges', () => {
  for (const address of [
    '0.0.0.0', '10.0.0.1', '100.64.0.1', '127.0.0.1', '169.254.169.254',
    '172.16.0.1', '192.0.2.1', '192.168.0.1', '198.18.0.1', '198.51.100.1',
    '203.0.113.1', '224.0.0.1', '255.255.255.255', '::', '::1', 'fc00::1',
    'fe80::1', 'ff02::1', '::ffff:127.0.0.1', '2001:db8::1',
  ]) assert.equal(isPublicIp(address), false, address);
});

test('rejects localhost without consulting DNS', async () => {
  await assert.rejects(resolvePublic('localhost'), /non-public destination/);
  await assert.rejects(resolvePublic('service.localhost'), /non-public destination/);
});

test('contains EPIPE to one tunnel instead of crashing the proxy process', () => {
  for (const failingSide of ['client', 'upstream']) {
    const client = new FakeSocket();
    const upstream = new FakeSocket();
    guardSocketPair(client, upstream);

    const error = Object.assign(new Error('write EPIPE'), { code: 'EPIPE' });
    (failingSide === 'client' ? client : upstream).emit('error', error);

    assert.equal(client.destroyed, true, `${failingSide}: client closed`);
    assert.equal(upstream.destroyed, true, `${failingSide}: upstream closed`);
    assert.equal(client.destroyCalls, 1, `${failingSide}: client closed once`);
    assert.equal(upstream.destroyCalls, 1, `${failingSide}: upstream closed once`);
  }
});
