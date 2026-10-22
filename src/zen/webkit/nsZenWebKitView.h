/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

#ifndef mozilla_ZenWebKitView_h_
#define mozilla_ZenWebKitView_h_

#include "nsIZenWebKitView.h"

namespace zen {

class nsZenWebKitView final : public nsIZenWebKitView {
  NS_DECL_ISUPPORTS
  NS_DECL_NSIZENWEBKITVIEW

 public:
  nsZenWebKitView() = default;

 private:
  ~nsZenWebKitView() = default;
};

}  // namespace zen

#endif
