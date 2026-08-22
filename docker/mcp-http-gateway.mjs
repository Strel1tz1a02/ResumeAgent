import http from 'node:http';

const server = http.createServer((request, response) => {
  const upstream = http.request({
    hostname: 'playwright-mcp',
    port: 8931,
    method: request.method,
    path: request.url,
    headers: { ...request.headers, host: 'playwright-mcp' },
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
    upstreamResponse.pipe(response);
  });
  upstream.on('error', () => {
    if (!response.headersSent) response.writeHead(502, { Connection: 'close' });
    response.end('Bad Gateway');
  });
  request.pipe(upstream);
});

server.on('clientError', (_error, socket) => {
  socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
});

server.listen(8931, '0.0.0.0', () => console.log('MCP HTTP gateway listening on 8931'));
