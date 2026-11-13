import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideIonicAngular } from '@ionic/angular';

import { PROFILE } from '../../testing/fixtures';
import { ProfilePage } from './profile.page';

function render() {
  TestBed.configureTestingModule({
    imports: [ProfilePage],
    providers: [provideIonicAngular(), provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(ProfilePage);
  const http = TestBed.inject(HttpTestingController);
  http.expectOne('/api/v1/profile').flush(PROFILE);
  fixture.detectChanges();
  return { fixture, page: fixture.componentInstance, http };
}

describe('ProfilePage', () => {
  it('shows skills by category and active and past projects in accordions', () => {
    const { fixture } = render();
    const el: HTMLElement = fixture.nativeElement;
    // 4 sections + one sub-accordion per skill type (3 types in the fixture)
    expect(el.querySelectorAll('ion-accordion').length).toBe(7);
    const types = [...el.querySelectorAll('[data-testid="skill-types"] ion-accordion')].map((a) =>
      a.getAttribute('data-category'),
    );
    expect(types).toEqual(['Hospitality', 'Community support', 'Practical trades']);
    expect(el.querySelector('[data-category="Practical trades"]')!.textContent).toContain('0 offered · 0 excluded');
    expect(el.textContent).toContain('2 offered · 0 excluded');
    const active = el.querySelector('[data-testid="active-projects"]')!.textContent;
    expect(active).toContain('Outreach Data Audit');
    expect(active).toContain('You suggested this');
    expect(active).toContain('You match: Spreadsheet and data analysis');
    const past = el.querySelector('[data-testid="past-projects"]')!.textContent;
    expect(past).toContain('Phishing Awareness Pop-up');
    expect(past).toContain('Rejected');
  });

  it('saves only when something changed, sending the version and every skill', () => {
    const { fixture, page, http } = render();
    expect(page.dirty()).toBe(false);
    page.setSkill('active-listening', 'exclude');
    fixture.detectChanges();
    expect(page.dirty()).toBe(true);
    expect(page.excludedCount()).toBe(1);
    expect(fixture.nativeElement.querySelector('[data-category="Community support"]').textContent).toContain(
      '0 offered · 1 excluded',
    );
    page.save();
    const req = http.expectOne('/api/v1/profile');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({
      display_name: 'Asha',
      version: 3,
      skills: { cooking: 'offers', 'active-listening': 'exclude', carpentry: 'none' },
      causes: { homelessness: 'prefer', environment: 'neutral' },
    });
    req.flush({ ...PROFILE, version: 4, skills: PROFILE.skills.map((s) => (s.id === 'active-listening' ? { ...s, state: 'exclude' } : s)) });
    expect(page.dirty()).toBe(false);
    expect(page.message()?.color).toBe('success');
  });

  it('reloads when the profile was changed elsewhere', () => {
    const { page, http } = render();
    page.setCause('environment', 'exclude');
    page.save();
    http.expectOne({ method: 'PUT', url: '/api/v1/profile' }).flush({ detail: 'stale' }, { status: 409, statusText: '' });
    expect(page.message()?.color).toBe('warning');
    http.expectOne({ method: 'GET', url: '/api/v1/profile' }).flush(PROFILE);
  });

  it('shows onboarding status and submits a draft profile for vetting', () => {
    TestBed.configureTestingModule({
      imports: [ProfilePage],
      providers: [provideIonicAngular(), provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(ProfilePage);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('/api/v1/profile').flush({ ...PROFILE, status: 'draft', status_label: 'Not yet submitted for vetting', can_submit_for_vetting: true });
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('[data-testid="status"]')!.textContent).toContain('Not yet submitted');
    expect(el.textContent).toContain('Submit for vetting');
    fixture.componentInstance.submitForVetting();
    http.expectOne({ method: 'POST', url: '/api/v1/me/vetting' }).flush({ status: 'screening' });
    expect(fixture.componentInstance.message()?.text).toContain('in screening');
    http.expectOne('/api/v1/profile').flush({ ...PROFILE, status: 'screening', status_label: 'In screening' });
  });

  it('shows the server validation message', () => {
    const { page, http } = render();
    page.displayName.set('');
    page.save();
    http
      .expectOne('/api/v1/profile')
      .flush({ detail: 'Display name must be 1–60 characters.' }, { status: 422, statusText: '' });
    expect(page.message()?.text).toBe('Display name must be 1–60 characters.');
  });
});
