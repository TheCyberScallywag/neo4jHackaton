import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, RouterStateSnapshot, UrlTree, provideRouter } from '@angular/router';

import { authGuard } from './auth.guard';
import meta from './meta.json';

function run(signedIn: boolean) {
  localStorage.clear();
  if (signedIn) localStorage.setItem(`${meta.slug}.token`, 'tok');
  TestBed.configureTestingModule({ providers: [provideRouter([]), provideHttpClient()] });
  return TestBed.runInInjectionContext(() =>
    authGuard({} as ActivatedRouteSnapshot, { url: '/matches' } as RouterStateSnapshot),
  );
}

describe('authGuard', () => {
  it('lets signed-in users through', () => expect(run(true)).toBe(true));
  it('sends everyone else to sign-in', () => expect((run(false) as UrlTree).toString()).toBe('/sign-in'));
});
