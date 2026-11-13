import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { SessionService } from './session.service';

/** Signed-in users through; everyone else to sign-in. */
export const authGuard: CanActivateFn = () =>
  inject(SessionService).signedIn() || inject(Router).createUrlTree(['/sign-in']);
