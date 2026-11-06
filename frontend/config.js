// Public configuration only. There is NO client secret in a SPA: never add one here.
// Default API base URL. It can be overridden from the page (saved in localStorage).
window.CAMPUS_CAFE_API = "https://xp4tl33vtf.execute-api.us-east-1.amazonaws.com";

// Auth0 (public values). Leave empty until an approved tenant exists: the menu stays
// public and order operations show "login not configured".
window.CAMPUS_CAFE_AUTH = {
  domain: "",      // e.g. "YOUR-TENANT.us.auth0.com"  (issuer is https://<domain>/)
  clientId: "",    // SPA application Client ID (public)
  audience: ""     // custom API identifier; same value as the API Gateway authorizer audience
};
