// typescript-eslint type-checks .ts files without the Vue language plugin; vue-tsc sees the real types.
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent
  export default component
}
