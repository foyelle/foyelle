/**
 * Sandboxed preload: the only bridge between the canvas renderer and the
 * main process. It exposes these few functions and nothing else.
 */
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";
import { IPC, type FoyelleBridge, type HarnessConfig, type RegisterSurfaceSpec, type ShortcutName, type SignInResult, type Transcript } from "../shared/bridge";

const bridge: FoyelleBridge = {
  platform: "electron",
  harness: () => ipcRenderer.invoke(IPC.harness) as Promise<HarnessConfig>,
  registerSurface: (spec: RegisterSurfaceSpec) =>
    ipcRenderer.invoke(IPC.registerSurface, {
      surfaceId: String(spec.surfaceId),
      html: String(spec.html),
      csp: spec.csp ?? null,
    }) as Promise<string>,
  unregisterSurface: (surfaceId: string) => ipcRenderer.invoke(IPC.unregisterSurface, String(surfaceId)) as Promise<void>,
  onShortcut: (cb: (name: ShortcutName) => void) => {
    const listener = (_e: IpcRendererEvent, name: ShortcutName) => cb(name);
    ipcRenderer.on(IPC.shortcut, listener);
    return () => {
      ipcRenderer.removeListener(IPC.shortcut, listener);
    };
  },
  transcribe: (wav: ArrayBuffer) => ipcRenderer.invoke(IPC.transcribe, new Uint8Array(wav)) as Promise<Transcript>,
  setZoom: (factor: number) => ipcRenderer.send(IPC.zoom, Number(factor)),
  signinBegin: () => ipcRenderer.invoke(IPC.signinBegin) as Promise<{ redirectUri: string }>,
  signinOpen: (authUrl: string) => ipcRenderer.invoke(IPC.signinOpen, String(authUrl)) as Promise<SignInResult>,
  signinEnd: () => ipcRenderer.invoke(IPC.signinEnd) as Promise<void>,
};

contextBridge.exposeInMainWorld("foyelle", bridge);
