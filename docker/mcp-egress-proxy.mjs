import dns from 'node:dns/promises';
import http from 'node:http';
import net from 'node:net';

const PORT = 3128;
const CONNECT_TIMEOUT_MS = 30_000;

const blockedV4Cidrs = [
  ['0.0.0.0', 8],
  ['10.0.0.0', 8],
  ['100.64.0.0', 10],
  ['127.0.0.0', 8],
  ['169.254.0.0', 16],
  ['172.16.0.0', 12],
  ['192.0.0.0', 24],
  ['192.0.2.0', 24],
  ['192.88.99.0', 24],
  ['192.168.0.0', 16],
  ['198.18.0.0', 15],
  ['198.51.100.0', 24],
  ['203.0.113.0', 24],
  ['224.0.0.0', 4],
  ['240.0.0.0', 4],
];

function ipv4ToInt(value) {
  const parts = value.split('.').map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return null;
  }
  return parts.reduce((result, part) => ((result << 8) | part) >>> 0, 0);
}

function inV4Cidr(value, network, bits) {
  const address = ipv4ToInt(value);
  const base = ipv4ToInt(network);
  if (address === null || base === null) return false;
  const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
  return (address & mask) === (base & mask);
}

function isBlockedV4(value) {
  const address = ipv4ToInt(value);
  if (address === null) return true;
  return blockedV4Cidrs.some(([network, bits]) => inV4Cidr(value, network, bits));
}

function expandIpv6(value) {
  const normalized = value.toLowerCase().split('%')[0];
  const mapped = normalized.match(/^(.*:)(\d+\.\d+\.\d+\.\d+)$/);
  let source = normalized;
  if (mapped) {
    const v4 = ipv4ToInt(mapped[2]);
    if (v4 === null) return null;
    source = `${mapped[1]}${(v4 >>> 16).toString(16)}:${(v4 & 0xffff).toString(16)}`;
  }
  const halves = source.split('::');
  if (halves.length > 2) return null;
  const left = halves[0] ? halves[0].split(':') : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  const missing = 8 - left.length - right.length;
  if (missing < 0 || (halves.length === 1 && missing !== 0)) return null;
  const parts = [...left, ...Array(missing).fill('0'), ...right];
  if (parts.length !== 8 || parts.some((part) => !/^[0-9a-f]{1,4}$/.test(part))) return null;
  return parts.map((part) => Number.parseInt(part, 16));
}

export function isPublicIp(value) {
  const family = net.isIP(value);
  if (family === 4) return !isBlockedV4(value);
  if (family !== 6) return false;
  const parts = expandIpv6(value);
  if (!parts) return false;

  if (parts.slice(0, 5).every((part) => part === 0) && parts[5] === 0xffff) {
    const embedded = `${parts[6] >>> 8}.${parts[6] & 255}.${parts[7] >>> 8}.${parts[7] & 255}`;
    return !isBlockedV4(embedded);
  }
  if (parts[0] === 0x2001 && parts[1] === 0x0db8) return false;
  return (parts[0] & 0xe000) === 0x2000;
}

export async function resolvePublic(hostname) {
  const normalized = hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (!normalized || normalized === 'localhost' || normalized.endsWith('.localhost')) {
    throw new Error('non-public destination');
  }
  const literalFamily = net.isIP(normalized);
  const answers = literalFamily
    ? [{ address: normalized, family: literalFamily }]
    : await dns.lookup(normalized, { all: true, verbatim: true });
  // Docker Desktop's DNS inhibition maps public names into 198.18.0.0/15.
  // Accept that range only as a DNS answer; literal URLs in the range stay blocked.
  const isAllowedAnswer = ({ address, family }) =>
    isPublicIp(address) || (!literalFamily && family === 4 && inV4Cidr(address, '198.18.0.0', 15));
  if (!answers.length || answers.some((answer) => !isAllowedAnswer(answer))) {
    throw new Error('non-public destination');
  }
  // Docker Desktop commonly has no IPv6 route even when DNS returns AAAA first.
  return answers.find(({ family }) => family === 4) ?? answers[0];
}

function destroyIfOpen(socket) {
  if (!socket.destroyed) socket.destroy();
}

/**
 * Keep a CONNECT tunnel failure local to that tunnel. Socket errors such as
 * EPIPE are normal when browsers cancel requests; without listeners Node
 * treats them as uncaught process-level errors.
 */
export function guardSocketPair(left, right) {
  let closing = false;
  const closePair = () => {
    if (closing) return;
    closing = true;
    destroyIfOpen(left);
    destroyIfOpen(right);
  };
  left.on('error', closePair);
  right.on('error', closePair);
  left.on('close', closePair);
  right.on('close', closePair);
  return closePair;
}

function reject(socketOrResponse, status = 403, message = 'Forbidden') {
  if (socketOrResponse instanceof net.Socket) {
    if (socketOrResponse.destroyed || socketOrResponse.writableEnded) return;
    socketOrResponse.once('error', () => destroyIfOpen(socketOrResponse));
    socketOrResponse.end(`HTTP/1.1 ${status} ${message}\r\nConnection: close\r\n\r\n`);
  } else if (!socketOrResponse.headersSent) {
    socketOrResponse.writeHead(status, { Connection: 'close' });
    socketOrResponse.end(message);
  }
}

const server = http.createServer(async (request, response) => {
  try {
    const target = new URL(request.url);
    if (target.protocol !== 'http:' || Number(target.port || 80) !== 80 || target.username || target.password) {
      reject(response);
      return;
    }
    const resolved = await resolvePublic(target.hostname);
    const headers = { ...request.headers, host: target.host, connection: 'close' };
    delete headers['proxy-authorization'];
    delete headers['proxy-connection'];
    const upstream = http.request({
      hostname: resolved.address,
      family: resolved.family,
      port: 80,
      method: request.method,
      path: `${target.pathname}${target.search}`,
      headers,
      timeout: CONNECT_TIMEOUT_MS,
    }, (upstreamResponse) => {
      upstreamResponse.on('error', () => response.destroy());
      response.on('close', () => upstreamResponse.destroy());
      response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    });
    upstream.on('timeout', () => upstream.destroy(new Error('upstream timeout')));
    upstream.on('error', () => {
      if (response.headersSent) response.destroy();
      else reject(response, 502, 'Bad Gateway');
    });
    request.on('aborted', () => upstream.destroy());
    request.on('error', () => upstream.destroy());
    response.on('error', () => upstream.destroy());
    request.pipe(upstream);
  } catch {
    reject(response);
  }
});

server.on('connect', async (request, clientSocket, head) => {
  let pendingUpstream;
  const closePendingUpstream = () => {
    if (pendingUpstream) destroyIfOpen(pendingUpstream);
  };
  // Guard the socket before awaiting DNS; the client may disconnect while
  // resolution is still in progress.
  clientSocket.on('error', closePendingUpstream);
  clientSocket.on('close', closePendingUpstream);
  try {
    const target = new URL(`http://${request.url}`);
    if (Number(target.port) !== 443 || target.username || target.password) {
      reject(clientSocket);
      return;
    }
    const resolved = await resolvePublic(target.hostname);
    if (clientSocket.destroyed) return;
    const upstream = net.connect({ host: resolved.address, family: resolved.family, port: 443 });
    pendingUpstream = upstream;
    upstream.setTimeout(CONNECT_TIMEOUT_MS, () => upstream.destroy(new Error('upstream timeout')));

    const rejectBeforeConnect = () => {
      upstream.off('error', rejectBeforeConnect);
      upstream.off('close', rejectBeforeConnect);
      pendingUpstream = undefined;
      reject(clientSocket, 502, 'Bad Gateway');
    };
    upstream.once('error', rejectBeforeConnect);
    upstream.once('close', rejectBeforeConnect);
    upstream.once('connect', () => {
      clientSocket.off('error', closePendingUpstream);
      clientSocket.off('close', closePendingUpstream);
      upstream.off('error', rejectBeforeConnect);
      upstream.off('close', rejectBeforeConnect);
      pendingUpstream = undefined;
      guardSocketPair(clientSocket, upstream);
      clientSocket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head.length) upstream.write(head);
      upstream.pipe(clientSocket);
      clientSocket.pipe(upstream);
    });
  } catch {
    reject(clientSocket);
  }
});

server.on('clientError', (_error, socket) => reject(socket, 400, 'Bad Request'));
if (process.argv[1]?.endsWith('mcp-egress-proxy.mjs')) {
  server.listen(PORT, '0.0.0.0', () => console.log(`MCP egress proxy listening on ${PORT}`));
}
