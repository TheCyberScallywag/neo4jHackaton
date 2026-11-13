// The container's nginx proxies /api to the api service, so the web build stays origin-relative.
// For a native (Capacitor) build, point this at the public API URL and add the app's origin
// to CORS_ORIGINS on the API.
export const environment = {
  production: true,
  apiUrl: '/api',
};
