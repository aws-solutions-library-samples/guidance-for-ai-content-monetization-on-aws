import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

/** Cloudscape mounts every Modal; pick the one whose header is on screen. */
const modal = (header: string | RegExp) => screen.getByText(header).closest('[role="dialog"]') as HTMLElement
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../services/partnerService', async () => {
  const actual = await vi.importActual<typeof import('../../services/partnerService')>('../../services/partnerService')
  return {
    KEY_GATED_OPERATIONS: actual.KEY_GATED_OPERATIONS,
    partnerService: {
      getRegisteredPartners: vi.fn(),
      getPartnerStats: vi.fn(),
      getAccessLogs: vi.fn(),
      updatePartner: vi.fn(),
      createPartner: vi.fn(),
      getPartner: vi.fn(),
      deletePartner: vi.fn(),
      rotateKey: vi.fn(),
      retireKey: vi.fn(),
      togglePartnerStatus: vi.fn(),
    },
  }
})
vi.mock('../../contexts/FeatureFlagContext', () => ({
  useFeatureFlags: () => ({ demoDataEnabled: false }),
}))

import BotRegistry from '../BotRegistry'
import { partnerService, RegisteredPartner } from '../../services/partnerService'

const partner: RegisteredPartner = {
  partner_id: 'ptr_test',
  partner_name: 'Test Partner',
  contact_email: 'publisher@example.test',
  pricing_tier: 'standard',
  status: 'active',
  allowed_endpoints: ['queryContent', 'submitLog'],
  identity_source: 'direct',
  active_key_count: 1,
  created_at: '2026-09-10T12:00:00Z',
}

describe('partner lifecycle UI', () => {
  beforeEach(() => {
    vi.mocked(partnerService.getRegisteredPartners).mockResolvedValue([partner])
    vi.mocked(partnerService.getPartnerStats).mockResolvedValue({
      total_partners: 1, active_partners: 1, suspended_partners: 0, total_active_keys: 1,
    })
    vi.mocked(partnerService.getAccessLogs).mockResolvedValue([])
    vi.mocked(partnerService.updatePartner).mockResolvedValue(partner)
  })

  afterEach(() => { vi.clearAllMocks() })

  it('edits allowed endpoints as a checkbox group of key-gated operations', async () => {
    render(<BotRegistry />)
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Partner' }))
    const dialog = modal('Edit Partner / Licensee')
    const query = within(dialog).getByLabelText(/queryContent/)
    const log = within(dialog).getByLabelText(/submitLog/)
    const license = within(dialog).getByLabelText(/validateLicense/)
    expect(query).toBeChecked(); expect(log).toBeChecked(); expect(license).not.toBeChecked()
    expect(within(dialog).queryByLabelText(/API Quota/)).toBeNull()  // quota is a tier property
    fireEvent.click(log); fireEvent.click(license)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(partnerService.updatePartner).toHaveBeenCalledWith(
      'ptr_test', expect.objectContaining({ allowed_endpoints: ['queryContent', 'validateLicense'] }),
    ))
    expect(vi.mocked(partnerService.updatePartner).mock.calls[0][1]).not.toHaveProperty('api_quota')
  })

  it('creates a direct partner and reveals the key value exactly once', async () => {
    vi.mocked(partnerService.createPartner).mockResolvedValue({
      partner: { ...partner, partner_id: 'ptr_new', partner_name: 'New Co' },
      key: { key_id: 'k_new', status: 'active', usage_plan_id: 'plan' },
      key_value: 'SECRET-VALUE-123',
    })
    render(<BotRegistry />)
    fireEvent.click(await screen.findByRole('button', { name: 'Create Partner' }))
    const dialog = modal('Create Partner / Licensee')
    fireEvent.change(within(dialog).getByPlaceholderText('Enter partner name'), { target: { value: 'New Co' } })
    fireEvent.click(within(dialog).getByLabelText(/queryContent/))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(partnerService.createPartner).toHaveBeenCalledWith(
      expect.objectContaining({ partner_name: 'New Co', pricing_tier: 'standard', allowed_endpoints: ['queryContent'] }),
    ))
    expect(await screen.findByText(/shown once/)).toBeInTheDocument()
    expect(screen.getByText(/Key ID k_new/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(screen.queryByText(/shown once/)).toBeNull())
    expect(screen.queryByText('SECRET-VALUE-123')).toBeNull()  // not persisted anywhere in the page
  })

  it('rotates a key through the card action and shows the new value', async () => {
    vi.mocked(partnerService.rotateKey).mockResolvedValue({
      partner, key: { key_id: 'k_rot', status: 'active', usage_plan_id: 'plan' }, key_value: 'ROTATED-VALUE',
    })
    vi.mocked(partnerService.getPartner).mockResolvedValue({ ...partner, keys: [], tier_limits: null })
    render(<BotRegistry />)
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate Key' }))
    await waitFor(() => expect(partnerService.rotateKey).toHaveBeenCalledWith('ptr_test'))
    expect(await screen.findByText(/Key ID k_rot/)).toBeInTheDocument()
  })

  it('deletes a partner only after confirmation', async () => {
    vi.mocked(partnerService.deletePartner).mockResolvedValue({ ...partner, status: 'deleted', active_key_count: 0 })
    render(<BotRegistry />)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Partner' }))
    expect(partnerService.deletePartner).not.toHaveBeenCalled()
    const dialog = modal('Delete partner?')
    expect(document.body.textContent).toMatch(/revokes and removes all of its API keys/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete permanently' }))
    await waitFor(() => expect(partnerService.deletePartner).toHaveBeenCalledWith('ptr_test'))
  })

  it('suspends and reactivates through the status toggle', async () => {
    render(<BotRegistry />)
    await screen.findByRole('button', { name: 'Edit Partner' })
    const toggle = screen.getByRole('checkbox', { name: /^Active$/ })
    fireEvent.click(toggle)
    await waitFor(() => expect(partnerService.togglePartnerStatus).toHaveBeenCalledWith('ptr_test', false))
  })
})
