/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#include "mozilla/nsZenWebKitView.h"

#include "ZenWebKitBridge.h"
#include "mozilla/WidgetUtils.h"
#include "nsIWidget.h"
#include "nsPIDOMWindow.h"
#include "nsString.h"

namespace zen {

NS_IMPL_ISUPPORTS(nsZenWebKitView, nsIZenWebKitView)

NS_IMETHODIMP
nsZenWebKitView::Create(mozIDOMWindowProxy* aWindow, const nsAString& aId,
                        const nsAString& aUrl) {
  NS_ENSURE_ARG_POINTER(aWindow);
  nsCOMPtr<nsPIDOMWindowOuter> outer = nsPIDOMWindowOuter::From(aWindow);
  NS_ENSURE_TRUE(outer, NS_ERROR_INVALID_ARG);
  RefPtr<nsIWidget> widget =
      mozilla::widget::WidgetUtils::DOMWindowToWidget(outer);
  NS_ENSURE_TRUE(widget, NS_ERROR_FAILURE);
  void* window = widget->GetNativeData(NS_NATIVE_WINDOW);
  NS_ENSURE_TRUE(window, NS_ERROR_FAILURE);
  return ZenWK_Create(window, NS_ConvertUTF16toUTF8(aId).get(),
                      NS_ConvertUTF16toUTF8(aUrl).get())
             ? NS_OK
             : NS_ERROR_FAILURE;
}

NS_IMETHODIMP
nsZenWebKitView::SetFrame(const nsAString& aId, double aX, double aY,
                          double aWidth, double aHeight, double aRadius) {
  return ZenWK_SetFrame(NS_ConvertUTF16toUTF8(aId).get(), aX, aY, aWidth,
                        aHeight, aRadius)
             ? NS_OK
             : NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP
nsZenWebKitView::SetVisible(const nsAString& aId, bool aVisible) {
  return ZenWK_SetVisible(NS_ConvertUTF16toUTF8(aId).get(), aVisible)
             ? NS_OK
             : NS_ERROR_NOT_AVAILABLE;
}

NS_IMETHODIMP
nsZenWebKitView::SetPassThrough(bool aPassThrough) {
  ZenWK_SetPassThrough(aPassThrough);
  return NS_OK;
}

NS_IMETHODIMP
nsZenWebKitView::SetPageFocused(bool aFocused) {
  ZenWK_SetPageFocused(aFocused);
  return NS_OK;
}

NS_IMETHODIMP
nsZenWebKitView::Destroy(const nsAString& aId) {
  ZenWK_Destroy(NS_ConvertUTF16toUTF8(aId).get());
  return NS_OK;
}

NS_IMETHODIMP
nsZenWebKitView::GetUrl(const nsAString& aId, nsAString& aRetval) {
  char buf[4096];
  NS_ENSURE_TRUE(ZenWK_GetUrl(NS_ConvertUTF16toUTF8(aId).get(), buf, sizeof buf),
                 NS_ERROR_NOT_AVAILABLE);
  CopyUTF8toUTF16(nsDependentCString(buf), aRetval);
  return NS_OK;
}

NS_IMETHODIMP
nsZenWebKitView::GetTitle(const nsAString& aId, nsAString& aRetval) {
  char buf[1024];
  NS_ENSURE_TRUE(
      ZenWK_GetTitle(NS_ConvertUTF16toUTF8(aId).get(), buf, sizeof buf),
      NS_ERROR_NOT_AVAILABLE);
  CopyUTF8toUTF16(nsDependentCString(buf), aRetval);
  return NS_OK;
}

}  // namespace zen
