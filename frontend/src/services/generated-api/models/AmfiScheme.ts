/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * An Indian mutual fund scheme (one plan + option) and its latest NAV.
 */
export type AmfiScheme = {
    /**
     * AMFI scheme code; fetch it with fetch_symbol "amfi:<code>".
     */
    scheme_code: string;
    /**
     * Full name, including plan (Direct/Regular) and option (Growth/IDCW).
     */
    scheme_name: string;
    /**
     * Latest NAV in INR; null if the scheme has none.
     */
    nav?: (string | null);
    nav_date?: (string | null);
};

