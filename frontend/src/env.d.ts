declare namespace NodeJS {
  interface ProcessEnv {
    REACT_APP_API_URL?: string;
  }
}
declare const process: { env: NodeJS.ProcessEnv };
