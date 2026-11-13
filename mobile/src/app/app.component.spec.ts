import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideIonicAngular } from '@ionic/angular';

import { AppComponent } from './app.component';
import { ME } from './testing/fixtures';
import meta from './core/meta.json';

function render(signedIn: boolean) {
  localStorage.clear();
  if (signedIn) localStorage.setItem(`${meta.slug}.token`, 'tok');
  TestBed.configureTestingModule({
    imports: [AppComponent],
    providers: [provideIonicAngular(), provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(AppComponent);
  fixture.detectChanges();
  return { fixture, http: TestBed.inject(HttpTestingController) };
}

describe('AppComponent', () => {
  it('offers sign-in when signed out', () => {
    const { fixture } = render(false);
    expect(fixture.nativeElement.textContent).toContain('Sign in');
  });

  it('loads the account and lists both organisations when signed in', () => {
    const { fixture, http } = render(true);
    http.expectOne('/api/v1/me').flush(ME);
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('Asha');
    expect(text).toContain('My matches');
    expect(fixture.nativeElement.querySelectorAll('ion-select-option').length).toBe(2);
    expect(fixture.nativeElement.querySelector('[data-testid="unread"]').textContent).toContain('3');
    expect(fixture.nativeElement.querySelector('[data-testid="status"]').textContent).toContain('Active');
  });
});
