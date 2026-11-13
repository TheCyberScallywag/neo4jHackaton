import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideIonicAngular } from '@ionic/angular';

import { OrgStore } from '../../core/org.store';
import { MATCHES, ME } from '../../testing/fixtures';
import { MatchesPage } from './matches.page';

function render() {
  TestBed.configureTestingModule({
    imports: [MatchesPage],
    providers: [provideIonicAngular(), provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
  });
  TestBed.inject(OrgStore).me.set(ME);
  const fixture = TestBed.createComponent(MatchesPage);
  fixture.detectChanges();
  return { fixture, http: TestBed.inject(HttpTestingController) };
}

describe('MatchesPage', () => {
  it('shows matched skills, preferred cause, remaining needs and own exclusions', () => {
    const { fixture, http } = render();
    http.expectOne('/api/v1/orgs/org_a/matches?preferred_only=false').flush(MATCHES);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Winter Outreach Kitchen');
    expect(text).toContain('Preferred cause');
    expect(text).toContain('Community cooking');
    expect(text).toContain('Van driving');
    expect(text).toContain('you excluded the cause Environment');
  });

  it('reloads with the preferred-causes-only filter', () => {
    const { fixture, http } = render();
    http.expectOne('/api/v1/orgs/org_a/matches?preferred_only=false').flush(MATCHES);
    fixture.componentInstance.preferredOnly.set(true);
    fixture.detectChanges();
    http.expectOne('/api/v1/orgs/org_a/matches?preferred_only=true').flush({ ...MATCHES, matches: [] });
  });

  it('says when results are withheld while the graph catches up', () => {
    const { fixture, http } = render();
    http
      .expectOne('/api/v1/orgs/org_a/matches?preferred_only=false')
      .flush({ withheld: 'Your latest profile change is still being applied.', matches: [], excluded: [] });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('[data-testid="withheld"]').textContent).toContain('still being applied');
  });
});
