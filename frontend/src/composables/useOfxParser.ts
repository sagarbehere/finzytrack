
import { ref } from 'vue'
import { parse } from 'ofx-js'
import type { OfxFileDetails, OFXTransaction } from '@/types/ofx'
import { toMoney } from '@/utils/money'

export function useOfxParser() {
  const selectedFile = ref<File | null>(null)
  const fileDetails = ref<OfxFileDetails | null>(null)
  const parseError = ref<string | null>(null)
  const isParsing = ref<boolean>(false)

  const processFile = async (file: File) => {
    if (!validateFile(file)) {
      parseError.value = 'Invalid file type. Please select an OFX or QFX file.'
      return
    }

    clearFile()
    selectedFile.value = file
    isParsing.value = true

    try {
      const fileContent = await readFileAsText(file)
      await parseOFXContent(fileContent)
    } catch (err: any) {
      console.error('Error processing OFX file:', err)
      const errorMessage = err.message || 'Failed to parse OFX file. Please check the file format.'
      parseError.value = errorMessage
    } finally {
      isParsing.value = false
    }
  }

  const clearFile = () => {
    selectedFile.value = null
    fileDetails.value = null
    parseError.value = null
    isParsing.value = false
  }

  const validateFile = (file: File): boolean => {
    const validExtensions = ['.ofx', '.qfx']
    const fileName = file.name.toLowerCase()
    return validExtensions.some((ext) => fileName.endsWith(ext))
  }

  const readFileAsText = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => resolve(e.target?.result as string)
      reader.onerror = () => reject(new Error('Failed to read file'))
      reader.readAsText(file)
    })
  }

  const parseOFXContent = async (content: string) => {
    try {
      const parsed = await parse(content)
      if (!parsed) throw new Error('Invalid OFX file format')
      fileDetails.value = extractFileDetails(parsed)
    } catch (err: any) {
      throw new Error(`OFX parsing failed: ${err.message}`)
    }
  }

  const getStatementDateRange = (tranlist: { DTSTART?: string; DTEND?: string; STMTTRN?: OFXTransaction | OFXTransaction[] }): { startDate: string | null; endDate: string | null } => {
    const parseOfxDate = (dateString: string | undefined): string | null => {
      if (!dateString || dateString.length < 8) return null;
      const year = dateString.substring(0, 4);
      const month = dateString.substring(4, 6);
      const day = dateString.substring(6, 8);
      return `${year}-${month}-${day}`;
    };

    let startDate = parseOfxDate(tranlist?.DTSTART);
    let endDate = parseOfxDate(tranlist?.DTEND);

    if ((!startDate || !endDate) && tranlist && tranlist.STMTTRN) {
      const transactions: OFXTransaction[] = Array.isArray(tranlist.STMTTRN)
        ? tranlist.STMTTRN
        : [tranlist.STMTTRN];

      if (transactions.length > 0) {
        const dates = transactions
          .map((t) => t.DTPOSTED)
          .filter((d): d is string => !!d)
          .map((d) => new Date(d.substring(0, 8).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')))
          .sort((a, b) => a.getTime() - b.getTime());

        if (dates.length > 0) {
          if (!startDate) {
            startDate = dates[0].toISOString().split('T')[0];
          }
          if (!endDate) {
            endDate = dates[dates.length - 1].toISOString().split('T')[0];
          }
        }
      }
    }

    return { startDate, endDate };
  }

  const extractFileDetails = (parsedOFX: Record<string, any>): OfxFileDetails => {
    const body = parsedOFX.OFX || parsedOFX
    const signon = body.SIGNONMSGSRSV1?.SONRS?.FI
    const stmt =
      body.BANKMSGSRSV1?.STMTTRNRS?.STMTRS || body.CREDITCARDMSGSRSV1?.CCSTMTTRNRS?.CCSTMTRS
    if (!stmt) throw new Error('No statement data found in OFX file')

    const acctfrom = stmt.BANKACCTFROM || stmt.CCACCTFROM
    const tranlist = stmt.BANKTRANLIST
    const transactions: OFXTransaction[] = tranlist?.STMTTRN ? (Array.isArray(tranlist.STMTTRN) ? tranlist.STMTTRN : [tranlist.STMTTRN]) : []


    let accountType = ''
    if (body.CREDITCARDMSGSRSV1) {
      accountType = ''
    } else if (body.BANKMSGSRSV1 && acctfrom?.ACCTTYPE) {
      accountType = acctfrom.ACCTTYPE
    }

    const { startDate, endDate } = getStatementDateRange(tranlist);

    const details: OfxFileDetails = {
      institution: signon?.ORG || 'Unknown',
      institutionFid: signon?.FID || null,
      accountType: accountType,
      accountId: acctfrom?.ACCTID || 'Unknown',
      currency: stmt.CURDEF || 'USD',
      transactionCount: transactions.length,
      startDate: startDate,
      endDate: endDate,
      balance: toMoney(stmt.LEDGERBAL?.BALAMT || stmt.AVAILBAL?.BALAMT || '0'),
      rawTransactions: transactions,
    };

    return details;
  }

  return {
    selectedFile,
    fileDetails,
    parseError,
    isParsing,
    processFile,
    clearFile,
  }
}
