import type { Matches, Me, Notification, Placement, Profile, Submission } from '../core/api.types';

export const ME: Me = {
  user: { id: 'u_1', display_name: 'Asha' },
  organisations: [
    { id: 'org_a', name: 'Cyber Scallywags', description: '', opted_in: true, reviewer: false },
    { id: 'org_b', name: 'DataKind UK', description: '', opted_in: true, reviewer: false },
  ],
  active_org_id: 'org_a',
  status: 'active',
  status_label: 'Active',
  system_roles: [],
  unread_notifications: 3,
};

export const MATCHES: Matches = {
  withheld: null,
  matches: [
    {
      project: 'p_1',
      title: 'Winter Outreach Kitchen',
      organisation: 'Cyber Scallywags',
      cause: 'Homelessness',
      preferred_cause: true,
      matched_skills: ['Active listening', 'Community cooking'],
      optional_skills: [],
      remaining_skills: ['First aid', 'Van driving'],
    },
  ],
  excluded: [{ project: 'p_2', title: 'Hidden Project', reasons: ['you excluded the cause Environment'] }],
};

export const SUBMISSIONS: Submission[] = [
  {
    id: 'p_3',
    title: 'Outreach Data Audit',
    state: 'submitted',
    org_name: 'DataKind UK',
    current_revision: 1,
    updated_at: '2026-11-13T10:00:00+00:00',
  },
];

export const PROFILE: Profile = {
  display_name: 'Asha',
  version: 3,
  skills: [
    { id: 'cooking', name: 'Community cooking', category: 'Hospitality', state: 'offers' },
    { id: 'active-listening', name: 'Active listening', category: 'Community support', state: 'offers' },
    { id: 'carpentry', name: 'Carpentry', category: 'Practical trades', state: 'none' },
  ],
  causes: [
    { id: 'homelessness', name: 'Homelessness', stance: 'prefer' },
    { id: 'environment', name: 'Environment', stance: 'neutral' },
  ],
  projects: {
    active: [
      { id: 'p_1', title: 'Outreach Data Audit', organisation: 'DataKind UK', relation: 'suggested', state: 'submitted', detail: null },
      {
        id: 'p_2',
        title: 'Food Bank Demand Dashboard',
        organisation: 'DataKind UK',
        relation: 'matched',
        state: 'published',
        detail: 'You match: Spreadsheet and data analysis',
      },
    ],
    past: [{ id: 'p_3', title: 'Phishing Awareness Pop-up', organisation: 'Cyber Scallywags', relation: 'suggested', state: 'rejected', detail: null }],
    matches_withheld: null,
  },
  status: 'active',
  status_label: 'Active',
  can_submit_for_vetting: false,
};

export const PLACEMENTS: Placement[] = [
  {
    project_id: 'p_2',
    volunteer_id: 'u_1',
    title: 'Food Bank Demand Dashboard',
    org_name: 'DataKind UK',
    state: 'eligible',
    state_label: 'Eligible: awaiting answers',
    volunteer_said: null,
    lead_said: null,
    matched_skills: ['Spreadsheet and data analysis'],
    version: 1,
  },
  {
    project_id: 'p_4',
    volunteer_id: 'u_1',
    title: 'Winter Outreach Kitchen',
    org_name: 'Cyber Scallywags',
    state: 'team',
    state_label: 'On the team',
    volunteer_said: 'yes',
    lead_said: 'yes',
    matched_skills: ['Community cooking'],
    version: 3,
  },
];

export const NOTIFICATIONS: Notification[] = [
  { id: 2, kind: 'placement', message: 'You are on the team for “Winter Outreach Kitchen”.', link: '/opportunities', created_at: '2026-11-13T10:00:00+00:00', read_at: null },
  { id: 1, kind: 'vetting', message: 'You are approved and active.', link: '/profile', created_at: '2026-11-12T10:00:00+00:00', read_at: '2026-11-12T11:00:00+00:00' },
];
