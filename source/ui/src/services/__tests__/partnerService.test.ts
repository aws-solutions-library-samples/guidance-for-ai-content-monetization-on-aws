import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('aws-amplify/auth', () => ({
  getCurrentUser: vi.fn(),
  fetchAuthSession: vi.fn(),
}))

import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'
import { partnerService } from '../partnerService'

describe('partner discovery configuration', () => {
  beforeEach(() => {
    vi.mocked(getCurrentUser).mockResolvedValue({ username: 'publisher' } as never)
    vi.mocked(fetchAuthSession).mockResolvedValue({
      tokens: { idToken: { toString: () => 'unit-test-token' } },
    } as never)
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('does not infer discovery permissions for missing or empty live configuration', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      partners: [
        { partner_id: 'missing' },
        { partner_id: 'empty', allowed_endpoints: [] },
        { partner_id: 'logging-only', allowed_endpoints: ['/log'] },
      ],
    }))))

    const partners = await partnerService.getRegisteredPartners()
    expect(partners.map(partner => partner.allowed_endpoints)).toEqual([[], [], ['/log']])
  })

  it('passes server endpoint grants through unchanged', async () => {
    const endpoints = ['queryContent', 'submitLog', 'customGrant']
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      partners: [{ partner_id: 'legacy', allowed_endpoints: endpoints }],
    }))))

    expect((await partnerService.getRegisteredPartners())[0].allowed_endpoints).toEqual(endpoints)
  })

  it('uses only canonical discovery in opt-in demo permissions', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))

    const partners = await partnerService.getRegisteredPartners(undefined, true)
    // Demo fixtures use the key-gated operationId vocabulary; [] means "all operations allowed".
    expect(partners.map(partner => partner.allowed_endpoints)).toEqual([
      ['queryContent', 'submitLog'],
      ['queryContent', 'submitLog'],
      [],
      ['queryContent'],
      ['queryContent'],
    ])
    await expect(partnerService.getRegisteredPartners()).rejects.toThrow('offline')
  })

  it('hides deleted partners from the registry list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      partners: [{ partner_id: 'live', status: 'active' }, { partner_id: 'gone', status: 'deleted' }],
    }))))
    expect((await partnerService.getRegisteredPartners()).map(p => p.partner_id)).toEqual(['live'])
  })
})

describe('partner lifecycle client', () => {
  beforeEach(() => {
    vi.mocked(getCurrentUser).mockResolvedValue({ username: 'publisher' } as never)
    vi.mocked(fetchAuthSession).mockResolvedValue({
      tokens: { idToken: { toString: () => 'unit-test-token' } },
    } as never)
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  // Keep only the contract path: drop the host and any API version prefix the env supplies.
  const calls = () => vi.mocked(fetch).mock.calls.map(([url, init]) => [
    new URL(String(url)).pathname.replace(/^\/v\d+/, ''), (init as RequestInit).method,
  ])

  it('issues the contract calls with the Cognito bearer token', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ partner: { partner_id: 'p' }, key: { key_id: 'k' }, key_value: 'v' }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ partner_id: 'p', keys: [] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ partner: { partner_id: 'p' }, key: { key_id: 'k2' }, key_value: 'v2' }), { status: 201 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ partner_id: 'p', status: 'deleted' })))
    vi.stubGlobal('fetch', fetchMock)

    const created = await partnerService.createPartner({ partner_name: 'P', pricing_tier: 'basic' })
    expect(created.key_value).toBe('v')
    await partnerService.getPartner('p')
    expect((await partnerService.rotateKey('p')).key?.key_id).toBe('k2')
    await partnerService.retireKey('p', 'k/1')
    expect((await partnerService.deletePartner('p')).status).toBe('deleted')

    expect(calls()).toEqual([
      ['/partners', 'POST'],
      ['/partners/p', 'GET'],
      ['/partners/p/keys', 'POST'],
      ['/partners/p/keys/k%2F1', 'DELETE'],
      ['/partners/p', 'DELETE'],
    ])
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toMatchObject({ Authorization: 'Bearer unit-test-token' })
  })

  it('surfaces the backend error message on a 409 conflict', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: 'key k is the only active key of partner p; rotate first', error_code: 'conflict' }), { status: 409 },
    )))
    await expect(partnerService.retireKey('p', 'k')).rejects.toThrow(/only active key/)
  })

  it('maps the status toggle to suspend / reactivate', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => new Response(JSON.stringify({ partner_id: 'p' }))))
    await partnerService.togglePartnerStatus('p', false)
    expect(JSON.parse((vi.mocked(fetch).mock.calls[0][1] as RequestInit).body as string)).toEqual({ status: 'suspended' })
    await partnerService.togglePartnerStatus('p', true)
    expect(JSON.parse((vi.mocked(fetch).mock.calls[1][1] as RequestInit).body as string)).toEqual({ status: 'active' })
  })
})
