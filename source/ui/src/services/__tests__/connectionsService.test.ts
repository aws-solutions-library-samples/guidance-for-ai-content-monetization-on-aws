/**
 * connectionsService — WordPress re-home slice (task 11.1)
 *
 * Covers the Demo_Toggle / mock-data honesty contract (#27) for the
 * content-domain connection lookup, and the multi-brand WordPress resolution
 * used by ContentConnections. The broader Connections page service surface is
 * owned by task 12.1.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// Auth headers are built via aws-amplify/auth; stub it out.
vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn().mockResolvedValue(null),
  fetchAuthSession: vi.fn().mockResolvedValue({ tokens: undefined }),
}));

import { connectionsService } from '../connectionsService';

const okJson = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response);
const httpError = (status: number) =>
  Promise.resolve({
    ok: false,
    status,
    statusText: `HTTP ${status}`,
    json: () => Promise.resolve({}),
  } as Response);

describe('connectionsService (WordPress re-home)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('allowMock gate (#27)', () => {
    it('OFF: rethrows on failure (no mock fabrication)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      await expect(connectionsService.getContentConnections(false)).rejects.toThrow();
    });

    it('OFF: returns [] on an empty-but-successful response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ connections: [] })));
      await expect(connectionsService.getContentConnections(false)).resolves.toEqual([]);
    });

    it('ON: falls back to mock content connections on failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(httpError(500)));
      const connections = await connectionsService.getContentConnections(true);
      expect(connections.length).toBeGreaterThan(0);
    });
  });

  describe('getActiveWordPressConnections (multi-brand, Req 6.3a)', () => {
    it('returns only active WordPress content connections, one per brand', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn().mockReturnValue(
          okJson({
            connections: [
              { connection_id: 'wp-acme', domain: 'content', adapter: 'wordpress', brand: 'acme', active: true },
              { connection_id: 'wp-globex', domain: 'content', adapter: 'wordpress', brand: 'globex', active: true },
              { connection_id: 'wp-inactive', domain: 'content', adapter: 'wordpress', brand: 'stale', active: false },
              { connection_id: 'other', domain: 'content', adapter: 'other-cms', brand: null, active: true },
            ],
          }),
        ),
      );

      const active = await connectionsService.getActiveWordPressConnections();
      expect(active.map((c) => c.connection_id)).toEqual(['wp-acme', 'wp-globex']);
      expect(active.every((c) => c.adapter === 'wordpress' && c.active)).toBe(true);
    });

    it('OFF: returns [] when no WordPress connection is active (empty state, no build flag)', async () => {
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(okJson({ connections: [] })));
      await expect(connectionsService.getActiveWordPressConnections(false)).resolves.toEqual([]);
    });
  });
});
