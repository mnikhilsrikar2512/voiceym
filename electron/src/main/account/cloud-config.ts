/** Voiceym Cloud endpoints (vssaas desktop-app-guide §1). One block per environment. */
export type CloudEnvName = 'prod' | 'dev' | 'local';

export interface CloudEnvironment {
  name: CloudEnvName;
  issuer: string;
  accountApi: string;
  syncApi: string;
  signedInPage: string;
  clientId: string;
}

const CLIENT_ID = 'voiceym-desktop-oss';

const ENVIRONMENTS: Record<CloudEnvName, Omit<CloudEnvironment, 'name' | 'clientId'>> = {
  prod: {
    issuer: 'https://voiceym.example/identity/realms/vssaas',
    accountApi: 'https://appapi.voiceym.example/v1/account',
    syncApi: 'https://appapi.voiceym.example/v1/sync',
    signedInPage: 'https://app.voiceym.example/app/desktop-signed-in',
  },
  dev: {
    issuer: 'https://dev-app.voiceym.example/identity/realms/vssaas',
    accountApi: 'https://dev-appapi.voiceym.example/v1/account',
    syncApi: 'https://dev-appapi.voiceym.example/v1/sync',
    signedInPage: 'https://dev-app.voiceym.example/app/desktop-signed-in',
  },
  local: {
    issuer: 'http://127.0.0.1:3000/identity/realms/vssaas',
    accountApi: 'http://127.0.0.1:8080/v1/account',
    syncApi: 'http://127.0.0.1:8090/v1/sync',
    signedInPage: 'http://127.0.0.1:3000/app/desktop-signed-in',
  },
};

export function resolveCloudEnvironment(opts: {
  packaged: boolean;
  requested?: string;
}): CloudEnvironment {
  const requested = opts.requested as CloudEnvName | undefined;
  const name: CloudEnvName = opts.packaged
    ? 'prod'
    : requested && Object.hasOwn(ENVIRONMENTS, requested)
      ? requested
      : 'dev';
  return { name, clientId: CLIENT_ID, ...ENVIRONMENTS[name] };
}

/** Sync origins join in S1; S0 talks only to the issuer and the account API. */
export function cloudOrigins(env: CloudEnvironment): Set<string> {
  return new Set([new URL(env.issuer).origin, new URL(env.accountApi).origin]);
}

/**
 * On in developer builds; packaged builds need VOICEYM_CLOUD_ACCOUNTS=1 at build
 * time, so releases ship it dark until the vssaas production API launches.
 */
export function cloudAccountsAvailable(packaged: boolean): boolean {
  return !packaged || (typeof __CLOUD_ACCOUNTS__ === 'boolean' && __CLOUD_ACCOUNTS__);
}
