import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { authInterceptor } from './auth.interceptor';
import meta from './meta.json';
import { SessionService } from './session.service';

function setup(signedIn: boolean) {
  localStorage.clear();
  if (signedIn) localStorage.setItem(`${meta.slug}.token`, 'tok');
  TestBed.configureTestingModule({
    providers: [provideRouter([]), provideHttpClient(withInterceptors([authInterceptor])), provideHttpClientTesting()],
  });
  const navigate = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
  return {
    http: TestBed.inject(HttpClient),
    backend: TestBed.inject(HttpTestingController),
    session: TestBed.inject(SessionService),
    navigate,
  };
}

describe('authInterceptor', () => {
  it('sends the bearer token to the API', () => {
    const { http, backend } = setup(true);
    http.get('/api/v1/me').subscribe();
    expect(backend.expectOne('/api/v1/me').request.headers.get('Authorization')).toBe('Bearer tok');
  });

  it('never sends the token anywhere else', () => {
    const { http, backend } = setup(true);
    http.get('https://elsewhere.example/x').subscribe();
    expect(backend.expectOne('https://elsewhere.example/x').request.headers.has('Authorization')).toBe(false);
  });

  it('forgets a revoked session and goes to sign-in', () => {
    const { http, backend, session, navigate } = setup(true);
    http.get('/api/v1/me').subscribe({ error: () => undefined });
    backend.expectOne('/api/v1/me').flush({}, { status: 401, statusText: '' });
    expect(session.signedIn()).toBe(false);
    expect(navigate).toHaveBeenCalledWith('/sign-in');
  });
});
