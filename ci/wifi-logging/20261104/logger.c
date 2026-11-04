void mtwf_dbg_prt(
	IN RTMP_ADAPTER	*pAd,
	IN const UINT32	dbgCat,
	IN const UINT32	dbgLvl,
	IN const INT8   *pFunc,
	IN const UINT32	line,
	IN const INT8   *pFmt,
	...)
{
	va_list args;
	INT8 strBuf[DBG_PRINT_BUF_SIZE];
	INT32 prefixLen = 0;
	INT32 avblBufLen = DBG_PRINT_BUF_SIZE;
	POS_COOKIE pObj = NULL;
	char *intf = NULL;
	char chip[5];
	UINT ifIndex, ifType;
	struct net_device *netDev = NULL;
	struct wifi_dev *wdev = NULL;
	int ret;

	if (pAd) {
		ret = snprintf(chip, sizeof(chip), "%04X", pAd->ChipID);
		if (os_snprintf_error(sizeof(chip), ret)) {
			MTWF_DBG(pAd, DBG_CAT_ALL, DBG_SUBCAT_ALL, DBG_LVL_ERROR,
				"snprintf error!\n");
			return;
		}
		pObj = (POS_COOKIE)pAd->OS_Cookie;
	}

	if (pObj) {
		ifIndex = pObj->ioctl_if;
		ifType = pObj->ioctl_if_type;

		if (ifType == INT_MAIN || ifType == INT_MBSSID) {
			if (pAd->hdev_ctrl && VALID_MBSS(pAd, ifIndex))
				wdev = &pAd->ApCfg.MBSSID[ifIndex].wdev;
		} else if (ifType == INT_APCLI) {
			if (ifIndex < MAX_MULTI_STA)
				wdev = &pAd->StaCfg[ifIndex].wdev;
		} else if (ifType == INT_WDS) {
			if (ifIndex < MAX_WDS_ENTRY)
				wdev = &pAd->WdsTab.WdsEntry[ifIndex].wdev;
		} else {
		}

		if (wdev) {
			netDev = (struct net_device *) wdev->if_dev;
			if (netDev)
				intf = netDev->name;
		}
	}

	/**
	* Log message format:
	* <chip>@[C<categore>][L<level>][<thread name>][<interface>],[<function>][<line>]: <log>
	* For example: 7915@C13L1P13656apcli0,MacTableInsertEntry() 920: XXXX
	*/
	prefixLen = snprintf(strBuf, avblBufLen,
						"%s@", intf?chip:"WiFi");
	if (mtwf_dbg_prtCatLvl && ((avblBufLen - prefixLen) > 0))
		prefixLen += snprintf(strBuf + prefixLen, avblBufLen - prefixLen,
							"C%02dL%1d", dbgCat, dbgLvl);
	if (mtwf_dbg_prtThreadId && ((avblBufLen - prefixLen) > 0))
		prefixLen += snprintf(strBuf + prefixLen, avblBufLen - prefixLen,
							"%s", current->comm);
	if (mtwf_dbg_prtIntfName && intf && ((avblBufLen - prefixLen) > 0))
		prefixLen += snprintf(strBuf + prefixLen, avblBufLen - prefixLen,
							"%s", intf);
	if (mtwf_dbg_prtFuncLine && ((avblBufLen - prefixLen) > 0))
		prefixLen += snprintf(strBuf + prefixLen, avblBufLen - prefixLen,
							",%s() %d", pFunc, line);
	if ((avblBufLen - prefixLen) > 0)
		prefixLen += snprintf(strBuf + prefixLen, avblBufLen - prefixLen,
							": ");

	if ((avblBufLen - prefixLen) > 0) {
		va_start(args, pFmt);
		ret = vsnprintf(strBuf + prefixLen, avblBufLen - prefixLen, pFmt, args);
		if (os_snprintf_error(avblBufLen - prefixLen, ret)) {
			MTWF_PRINT("%s: vsnprintf error\n", __func__);
		}
		va_end(args);
	}

	switch (dbgLvl)	{
	case DBG_LVL_OFF:
		MTWF_PRINT_DBG_LVL_OFF("%s", strBuf);
		break;
	case DBG_LVL_ERROR:
		MTWF_PRINT_DBG_LVL_ERROR("%s", strBuf);
		break;
	case DBG_LVL_WARN:
		MTWF_PRINT_DBG_LVL_WARN("%s", strBuf);
		break;
	case DBG_LVL_NOTICE:
		MTWF_PRINT_DBG_LVL_NOTICE("%s", strBuf);
		break;
	case DBG_LVL_INFO:
		MTWF_PRINT_DBG_LVL_INFO("%s", strBuf);
		break;
	case DBG_LVL_DEBUG:
	default:
		MTWF_PRINT_DBG_LVL_DEBUG("%s", strBuf);
		break;
	}
}
