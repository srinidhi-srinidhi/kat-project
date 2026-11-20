"""Terms and Conditions Utilities - legal text, banners, connect subscriptions and media uploads.

Domain layer behind /api/info: holds the English and Tamil Terms and Conditions HTML, reads etown_banner rows, tracks in-progress connect rows in etown_subscriptions, and uploads banner images and general media files to Azure Blob Storage.

Features:
    - Static Legal Text: Full English (ENGLISH_TAC) and Tamil (TAMIL_TAC) Terms & Conditions HTML
    - Banner Retrieval: Fetches active banners, hiding onboarding banners from existing providers
    - Subscription Tracking: Fetch/create/update in-progress "connect" etown_subscriptions rows
    - Status Normalization: Canonicalises client subscription status spellings (e.g. 'inprogess')
    - Banner Image Upload: Uploads image bytes to the Azure Blob 'banners' folder
    - Generic File Upload: Stores any image, video or document in the Azure Blob 'media' folder

Classes:
    No classes defined (module exposes constants and async/sync utility functions)

Functions:
    fetch_banners(): Fetch banner rows, excluding onboarding banners for existing providers
    fetch_inprogress_subscriptions(): Fetch a user profile's in-progress etown_subscriptions rows
    normalize_subscription_status(): Canonicalise a client-supplied subscription status spelling
    add_or_update_subscription(): Insert or update an etown_subscriptions row between two profiles
    upload_info_image(): Upload image bytes to the blob 'banners' folder and return its URL
    _media_extension(): Lowercased alphanumeric extension of a filename
    resolve_media_content_type(): Pick a MIME type from magic bytes, the declared type or the extension
    _media_blob_name(): Unique blob name that keeps the original filename
    upload_media_file(): Upload any file's bytes to the blob 'media' folder and return its URL

Dependencies:
    - re: Filename sanitising for generated blob names
    - uuid: Random suffix that keeps concurrent uploads of one filename distinct
    - app.db.client: Database access for banners and subscriptions
    - app.core.config: Azure Blob Storage container configuration
    - app.core.logging: Centralised logging for upload failures
    - app.utils.blob_storage: MIME detection and blob upload/URL helpers

Updates:
    13-May-2026: Initial implementation; added Tamil TAC and updated response structure.
    27-Jul-2026: Added upload_info_image(), storing an image in the blob banners folder.
    29-Jul-2026: Added normalize_subscription_status() for the inprogess spelling clients send.
    04-Sep-2026: Added upload_media_file() and resolve_media_content_type() for POST /info/upload.
"""

import re
import uuid
from typing import Optional

from ...core.config import settings
from ...core.logging import logger
from ...db.client import Database
from ...utils.blob_storage import (
    current_timestamp_prefix,
    detect_mime_type,
    normalize_container_name,
    to_public_blob_url,
    upload_blob_bytes,
)

BANNER_BLOB_FOLDER = "banners"

ENGLISH_TAC = """
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        line-height: 1.20; /* tighter overall */
        color: #333;
        background-color: #ffffff;
        margin: 0;
        padding: 0;
    }

    main {
        padding: 2px 12px 6px 12px; /* compact spacing */
    }

    p, li {
        text-align: justify;
        text-align-last: left;
        hyphens: auto;
        margin: 2px 0;
        line-height: 1.22; /* tighter text lines */
    }

  h2 {
    font-size: 16px; /* reduced from 18px */
    font-weight: 600;
    color: #000;
    margin-top: 10px; /* increased space above the heading */
    margin-bottom: 0px;
    padding: 0;
    line-height: 1.20; /* keep compact */
}


    h2 + p,
    h2 + ul {
        margin-top: 0;
    }

    ol, ul {
        padding-left: 15px;
        margin: 1px 0;
    }

    li {
        margin-bottom: 2px;
    }

    section {
        margin-bottom: 5px; /* tighter section spacing */
    }

    .last-updated {
        font-size: 11px;
        color: #666;
        font-style: italic;
        margin: 3px 0 8px 0; /* ⭐ small top space for neat alignment */
        line-height: 1.05; /* extremely tight text */
    }
</style>


<main>

<p class="last-updated"><em>Last updated on 24 Jun 2025</em></p>

<section>
    <p>Welcome to the etown mobile application. These Terms and Conditions govern your use of the etown mobile app and any related services. By using the App, you agree to these Terms. Your use of the App is also governed by our Privacy Policy, which can be accessed. We grant you a limited, non-exclusive, non-transferable, and revocable license to use the App for personal, non-commercial purposes.</p>
</section>

<section>
    <h2>Restrictions</h2>
    <p>In addition to all other terms and conditions of this Agreement, you shall not:</p>
    <ol>
        <li>Transfer the Services or otherwise make it available to any third party;</li>
        <li>Provide any service based on the Services without prior written permission;</li>
        <li>Allow user licenses to be shared or used by more than one individual other than by way of reassigning the user license to a new user;</li>
        <li>Except as permitted under applicable law, attempt to disassemble, reverse engineer or decompile the Services;</li>
        <li>Use the third party links to sites without agreeing to their website terms & conditions;</li>
        <li>Post links to third party sites or use their logo, company name, etc. without their prior written permission;</li>
        <li>Attempt to gain unauthorized access to the Services or its related systems or network;</li>
        <li>Use the Services in any manner that could damage, disable, overburden, impair or harm any server, network, computer system, resource of etown;</li>
        <li>Use the Services to send or store material containing software viruses, worms or other harmful computer codes, files, scripts or programs;</li>
        <li>Use the Services in any manner that interferes with or disrupts the integrity, security or performance of the Services, its components and the data contained therein;</li>
        <li>Host, display, upload, modify, publish, transmit, store, update or share any information that belongs to another person or entity and to which you do not have any right, including personal or confidential information of any person or entity with respect to which you do not have consent or permission from such person or entity;</li>
        <li>Violate any applicable local, state, national or international law;</li>
        <li>Use the Services for any form of competitive or benchmarking purposes;</li>
        <li>Remove or obscure any proprietary or other notices contained in the Services;</li>
        <li>Use our Services in any manner that threatens the unity, integrity, defence, security or sovereignty of India, friendly relations of India with other countries, or public order, or causes incitement to the commission of any cognisable offence or prevents investigation of any offence or is insulting other countries;</li>
        <li>Create a false identity to mislead any person as to the identity or origin of any communication;</li>
        <li>Use the services for transmitting information that is patently false and untrue, and is written or published in any form, with the intent to mislead or harass a person, entity or agency for financial gain or to cause any injury to any person;</li>
        <li>Use the services in a manner that relates to or encourages any activity prohibited by law in India.</li>
    </ol>
</section>

<section>
    <h2>Account</h2>
    <p>To access certain features of the App, you may be required to create an account. You are responsible for maintaining the confidentiality of your account information and are liable for any activity that occurs under your account.</p>
</section>

<section>
    <h2>Content Submission</h2>
    <p>The App may allow you to upload, post, or submit content. You retain ownership of your content, but by submitting it, you grant us a worldwide, royalty-free, and non-exclusive license to use, reproduce, modify, adapt, publish, translate, distribute, and display your content.</p>
</section>

<section>
    <h2>Termination</h2>
    <p>We reserve the right to terminate or suspend your account or access to the App at our discretion and without notice, for any reason, including a breach of these Terms.</p>
</section>

<section>
    <h2>Disclaimer of Warranties</h2>
    <p>You expressly understand and agree that the use of the services is at your sole risk. The services are provided on an <em>as-is</em> and <em>as-available</em> basis. Etown expressly disclaims all warranties of any kind, whether express or implied, including, but not limited to, the implied warranties of merchantability and fitness for a particular purpose. Etown makes no warranty that the services will be uninterrupted, timely, secure, or error-free. Use of any material downloaded or obtained through the use of the Services shall be at your own discretion and risk, and you will be solely responsible for any damage to your computer system, mobile telephone, wireless device, or data that results from the use of the services or the download of any such material. No advice or information, whether written or oral, obtained by you from etown, its employees or representatives shall create any warranty not expressly stated in the agreement.</p>
</section>

<section>
    <h2>Limitation of Liability</h2>
    <p>You agree that etown shall, in no event, be liable for any consequential, incidental, indirect, special, punitive, or other loss or damage whatsoever, or for loss of business profits, business interruption, computer failure, loss of business information, or other loss arising out of or caused by your use of, or inability to use the service, even if etown has been advised of the possibility of such damage. In no event shall etown's entire liability to you in respect of any service, whether direct or indirect, exceed the maximum subscription amount for the last 12 months, which shall be the limit of such liability.</p>
</section>

<section>
    <h2>Indemnification</h2>
    <p>You agree to indemnify and hold harmless etown, its officers, directors, employees, suppliers, and affiliates, from and against any losses, damages, fines, and expenses (including attorney's fees and costs) arising out of or relating to any claims that you have used the Services in violation of another party's rights, in violation of any law, in violations of any provisions of the Agreement, or any other claim related to your use of the Services, except where such use is authorized by etown.</p>
</section>

<section>
    <h2>Governing Law and Jurisdiction</h2>
    <p>Any controversy or claim arising out of or relating to the Terms shall be settled and adjudicated exclusively by the courts of Thoothukudi in accordance with the laws of India without regard to conflict of law principles.</p>
</section>

<section>
    <h2>Changes to Terms</h2>
    <p>We may update, change, or discontinue the App or these Terms at any time. Your continued use of the App after such changes constitutes your acceptance. We are not liable for any damages or losses resulting from your use of the App. This limitation includes direct, indirect, consequential, and incidental damages.</p>
</section>

<section>
    <p><strong>By using the etown mobile app, you agree to abide by these Terms and Conditions.</strong></p>
</section>

</main>

"""

TAMIL_TAC = """
   <style>
    body {
        font-family: 'Noto Sans Tamil', 'Lohit Tamil', 'Tamil Sangam MN', sans-serif;
        line-height: 1.55;
        color: #333;
        background-color: #ffffff;
        margin: 0;
        padding: 0;
    }

    main {
        padding: 6px 14px 16px 14px; /* reduced TOP padding */
    }

    /* UPDATED HERE */
    p, li {
        font-size: 13px;          /* smaller */
        line-height: 1.45;        /* tighter lines */
        text-align: left;
        text-align-last: left;
        text-justify: inter-character;  /* important for Tamil */
        hyphens: auto;
        word-break: break-word;

        /* FIX FOR EXTRA SPACING */
        word-spacing: -0.8px;
        letter-spacing: 0px;

        margin: 6px 0;
    }

    h2 {
    font-size: 16px; /* reduced from 18px */
    font-weight: 600;
    color: #000;
    margin-top: 10px; /* increased space above the heading */
    margin-bottom: 0px;
    padding: 0;
    line-height: 1.20; /* keep compact */
    }


    h2 + p,
    h2 + ul {
        margin-top: 0;
    }

    ol, ul {
        padding-left: 15px;
        margin: 1px 0;
    }
   li {
        margin-bottom: 2px;
    }

   section {
        margin-bottom: 5px; /* tighter section spacing */
    }

   .last-updated {
        font-size: 11px;
        color: #666;
        font-style: italic;
        margin: 3px 0 8px 0; /* ⭐ small top space for neat alignment */
        line-height: 1.05; /* extremely tight text */
    }
</style>


<main>

<p class="last-updated"><em>கடைசியாக 24 ஜூன் 2025 அன்று புதுப்பிக்கப்பட்டது</em></p>

<section>
    <p>Etown மொபைல் பயன்பாட்டிற்கு வரவேற்கிறோம். இந்த விதிமுறைகள் மற்றும் நிபந்தனைகள், Etown மொபைல் பயன்பாடு (App) மற்றும் அதனுடன் தொடர்புடைய அனைத்து சேவைகளின் பயன்பாட்டை ஒழுங்குபடுத்துகின்றன. App-ஐ அணுகி பயன்படுத்துவதன் மூலம், இவ்விதிமுறைகள் மற்றும் நிபந்தனைகளுக்கு உடன்படுவதாகக் கருதப்படுவீர்கள். உங்கள் App பயன்பாடு எங்கள் தனியுரிமைக் கொள்கை (Privacy Policy) யினாலும் நிர்வகிக்கப்படும்; அதை நீங்கள் எப்போது வேண்டுமானாலும் பார்வையிடலாம். Etown நிறுவனம், App-ஐ உங்கள் தனிப்பட்ட மற்றும் வாணிபமற்ற பயன்பாட்டிற்காக மட்டுமே பயன்படுத்தும் வகையில், உங்களுக்கு வரையறுக்கப்பட்ட, பிரத்யேகமற்ற, மாற்ற முடியாத, மற்றும் எந்நேரத்திலும் ரத்து செய்யக்கூடிய உரிமத்தை வழங்குகிறது.</p>
</section>

<section>
    <h2>கட்டுப்பாடுகள்</h2>
    <p>இந்த ஒப்பந்தத்தில் குறிப்பிடப்பட்ட அனைத்து விதிமுறைகள் மற்றும் நிபந்தனைகளுக்கு மேலாக, நீங்கள் பின்வரும் செயல்களைச் செய்யக் கூடாது:</p>
    <ul>
        <li>சேவையை மற்றவர்களுக்கு விற்றல் அல்லது பகிர்தல்.</li>
        <li>முன் அனுமதி இல்லாமல் சேவையை வர்த்தக நோக்கத்திற்காக பயன்படுத்தல்.</li>
        <li>ஒரே கணக்கை பலர் பகிர்ந்து பயன்படுத்தல் (புதிய பயனருக்கு மாற்றுவது தவிர).</li>
        <li>சட்டப்படி அனுமதி இல்லாமல் சேவையை பிரித்தெடுத்து ஆய்வு செய்தல், ரிவர்ஸ் எஞ்சினியர் செய்தல் அல்லது டிகம்பைல் செய்தல்.</li>
        <li>மூன்றாம் தரப்பு இணையதள விதிகளை பின்பற்றாமல் பயன்படுத்தல்.</li>
        <li>மூன்றாம் தரப்பின் லோகோ, பெயர், இணைப்புகளை அனுமதி இல்லாமல் பகிர்வு செய்தல் அல்லது பயன்படுத்தல்.</li>
        <li>சேவைகள் அல்லது நெட்வொர்க்கிற்கு அங்கீகரிக்கப்படாத அணுகலை முயற்சித்தல்.</li>
        <li>சேவைகளை சேவையகம், நெட்வொர்க், கணினி அமைப்பு அல்லது Etown வளங்களுக்கு சேதம் செய்யும் அல்லது அதிக சுமை கொடுக்கும் வகையில் பயன்படுத்தல்.</li>
        <li>வைரஸ், வோர்ம்கள், ஹேக்கிங் கோப்புகள் அல்லது தீங்கு விளைவிக்கும் கோப்புகளை அனுப்பல்/சேமித்தல்.</li>
        <li>சேவையின் செயல்திறன், பாதுகாப்பு அல்லது ஒருமைப்பாட்டை பாதிக்கும் விதமாக பயன்படுத்தல்.</li>
        <li>உங்களுக்குரிய உரிமை இல்லாத தகவல்களை பதிவேற்றல், பகிர்வு, வெளியீடு, மாற்றல்.</li>
        <li>உள்ளூர், மாநில, தேசிய அல்லது சர்வதேச சட்டங்களை மீறல்.</li>
        <li>சேவையை போட்டி அல்லது ஒப்பீட்டுக்காக பயன்படுத்தல்.</li>
        <li>சேவையில் உள்ள உரிமை குறிப்பு அல்லது சட்ட அறிவிப்புகளை நீக்கல்/மறைத்தல்.</li>
        <li>இந்தியா அல்லது பிற நாடுகளை பாதிக்கும், அவமதிக்கும், குற்றத்தை தூண்டுவதாக செயல்படக்கூடாது.</li>
        <li>போலி அடையாளம் உருவாக்கல் அல்லது உங்கள் அடையாளத்தை தவறாக காட்டல்.</li>
        <li>உண்மையற்ற தகவல் பகிர்வு, நிதி பலன் பெறல், யாருக்காவது சேதம் செய்யும் முயற்சி.</li>
        <li>இந்தியாவில் சட்டவிரோத செயல்களை ஊக்குவிப்பது அல்லது அதற்கான செயல்பாடு.</li>
    </ul>
</section>

<section>
    <h2>கணக்கு</h2>
    <ul>
        <li>App-இன் சில அம்சங்களை பயன்படுத்த, நீங்கள் உங்கள் தனிப்பட்ட கணக்கை உருவாக்க வேண்டியிருக்கலாம்.</li>
        <li>உங்கள் கணக்கு தகவலை ரகசியமாகக் காத்துக்கொள்ளுவது உங்கள் பொறுப்பு.</li>
        <li>உங்கள் கணக்கின் கீழ் நடக்கும் எல்லா செயல்களுக்கும் நீங்கள் முழுமையாக பொறுப்பானவர்.</li>
    </ul>
</section>

<section>
    <h2>உள்ளடக்கம் சமர்ப்பித்தல்</h2>
    <ul>
        <li>App உங்களுக்கு உங்கள் தகவல்கள், கோப்புகள் அல்லது படங்களை பதிவேற்றவும் பகிரவும் அனுமதிக்கலாம்.</li>
        <li>உங்கள் உள்ளடக்கத்தின் உரிமை நீங்கள் வைத்திருப்பீர்கள், ஆனால் அதை App-ல் சமர்ப்பிப்பதன் மூலம், Etown அதைப் பயன்படுத்த, பகிர, மாற்ற, வெளியிட, மொழிபெயர்க்க, காட்சிப்படுத்த உரிமை பெறும்.</li>
        <li>இதற்காக Etown உங்கள் தனிப்பட்ட அனுமதி கேட்காமல் இந்த உரிமையை பயன்படுத்தலாம், அது உலகளாவிய, கட்டணமில்லாத, பிரத்யேகமற்ற உரிமை ஆகும்.</li>
    </ul>
</section>

<section>
    <h2>முடித்தல்</h2>
    <ul>
        <li>நீங்கள் இந்த விதிமுறைகளை மீறினால், Etown உங்கள் கணக்கை முடிக்கலாம் அல்லது App-ஐ பயன்படுத்த முடியாமல் செய்யலாம்.</li>
        <li>இதற்கு முன் எச்சரிக்கை வழங்கப்பட தேவையில்லை; அதாவது, உங்கள் கணக்கு உடனடியாக முடக்கப்படலாம்.</li>
    </ul>
</section>

<section>
    <h2>உத்தரவாத மறுப்பு</h2>
    <ul>
        <li>Etown சேவைகளை நீங்கள் உங்கள் சொந்த ஆபத்திலும் பொறுப்பிலும் பயன்படுத்துகிறீர்கள்.</li>
        <li>எங்கள் சேவைகள் "இருப்பது போல்" மற்றும் "கிடைக்கும் போல்" வழங்கப்படுகின்றன.</li>
        <li>Etown எந்தவொரு உத்தரவாதமும் தரவில்லை.</li>
        <li>எங்கள் சேவைகள் சிறப்பு தேவைகளுக்கு பொருந்தும் என்று உறுதி இல்லை.</li>
        <li>App மூலம் பதிவிறக்கும் கோப்புகள் அனைத்தும் உங்கள் ஆபத்தில்.</li>
        <li>உங்கள் சாதனத்திற்கு சேதம் ஏற்பட்டால் பொறுப்பு உங்களுக்கே.</li>
        <li>Etown வழங்கும் ஆலோசனை கூட உத்தரவாதம் ஆகாது.</li>
    </ul>
</section>

<section>
    <h2>பொறுப்பு வரம்பு</h2>
    <p>Etown சேவையைப் பயன்படுத்தும் போது உங்களுக்கு ஏற்படும் சில பிரச்சினைகள் அல்லது இழப்புகளுக்கு Etown பொறுப்பல்ல. உதாரணமாக:</p>
    <ul>
        <li>உங்கள் வணிக லாபம் குறைதல்,</li>
        <li>வேலை அல்லது சேவை தடைபடுதல்,</li>
        <li>உங்கள் கணினி அல்லது மொபைல் வேலை செய்யாமல் போதல்,</li>
        <li>தரவு/தகவல் இழப்பு.</li>
    </ul>
    <p>எப்படிப் பிரச்சினை ஏற்பட்டாலும், Etown-இன் பொறுப்பு, நீங்கள் கடந்த 12 மாதங்களில் செலுத்திய சந்தா கட்டணத்தைத் தாண்டாது.</p>
</section>

<section>
    <h2>இழப்பீடு</h2>
    <p>நீங்கள் Etown சேவைகளைப் பயன்படுத்தும் போது:</p>
    <ul>
        <li>மற்றவரின் உரிமையை மீறினால்,</li>
        <li>சட்டத்தை மீறினால்,</li>
        <li>அல்லது இந்த விதிமுறைகளை மீறினால்,</li>
    </ul>
    <p>அதனால் Etown-க்கு ஏற்படும் இழப்புகள், அபராதங்கள், செலவுகள் அனைத்திற்கும் நீங்கள் பொறுப்பானவராக இருப்பீர்கள்.</p>
</section>

<section>
    <h2>சட்டம் மற்றும் நீதித்துறை</h2>
    <ul>
        <li>இந்த விதிமுறைகள் அனைத்தும் இந்திய சட்டத்தின் கீழ் வரும்.</li>
        <li>ஏதேனும் பிரச்சினை ஏற்பட்டால், அது தூத்துக்குடி நீதிமன்றத்தில் மட்டுமே தீர்க்கப்படும்.</li>
    </ul>
</section>

<section>
    <h2>விதிமுறைகளில் மாற்றங்கள்</h2>
    <ul>
        <li>Etown எப்போது வேண்டுமானாலும் App-இல் அல்லது இந்த விதிமுறைகளில் மாற்றம் செய்யலாம்.</li>
        <li>மாற்றங்களுக்குப் பிறகு App-ஐ பயன்படுத்தினால், புதிய விதிகளை ஏற்றுக்கொண்டதாகக் கருதப்படும்.</li>
        <li>App பயன்பாட்டால் ஏற்படும் சேதங்களுக்கு Etown பொறுப்பு ஏற்காது.</li>
    </ul>
</section>

<section>
    <h2>ஒப்புதல்</h2>
    <ul> 
        <li>நீங்கள் Etown மொபைல் App-ஐ பயன்படுத்தினால், இந்த விதிமுறைகள் மற்றும் நிபந்தனைகளை ஏற்றுக்கொள்கிறீர்கள் எனக் கருதப்படும்.</li>
    </ul>
</section>

<section>
    <p><strong>Etown மொபைல் பயன்பாட்டைப் பயன்படுத்துவதன் மூலம், இந்த விதிமுறைகள் மற்றும் கட்டுப்பாடுகளை நீங்கள் பின்பற்ற ஒப்புக்கொள்கிறீர்கள்.</strong></p>
</section>

</main>
"""


async def fetch_banners(contactid: str) -> list[dict]:
    """Fetch banners for a contact, hiding provider onboarding banners when the contact already has a provider profile."""
    is_provider_query = """
    SELECT 1
    FROM public.etown_userprofile
    WHERE contactid = $1
        AND role = 1
    LIMIT 1;
    """
    provider_rows = await Database.fetch(is_provider_query, contactid)
    is_provider = bool(provider_rows)

    if is_provider:
        query = """
        SELECT
            bannerid,
            type,
            image
        FROM public.etown_banner
        WHERE type NOT IN ('provider_onboard1', 'provider_onboard2');
        """
        rows = await Database.fetch(query)
    else:
        query = """
        SELECT
            bannerid,
            type,
            image
        FROM public.etown_banner;
        """
        rows = await Database.fetch(query)

    return [dict(row) for row in rows]


async def fetch_inprogress_subscriptions(current_userprofileid: str) -> list[dict]:
    """Fetch in-progress subscriptions for a user profile."""
    query = """
    SELECT
        subscriptionsid,
        current_userprofileid,
        current_userprofileidname,
        opponent_userprofileid,
        opponent_userprofileidname,
        subscriptionstatus
    FROM public.etown_subscriptions
    WHERE
        current_userprofileid = $1
        AND subscriptionstatus = 'inprogress';
    """
    rows = await Database.fetch(query, current_userprofileid)
    return [dict(row) for row in rows]


_SUBSCRIPTION_STATUS_ALIASES = {
    "inprogess": "inprogress",
    "inprogres": "inprogress",
    "in progress": "inprogress",
    "in-progress": "inprogress",
    "in_progress": "inprogress",
    "inprogress": "inprogress",
    "complete": "completed",
    "completed": "completed",
}


def normalize_subscription_status(status: str | None) -> str:
    """Return the canonical spelling of a subscription status, or the trimmed input when unrecognised."""
    cleaned = (status or "").strip()
    return _SUBSCRIPTION_STATUS_ALIASES.get(cleaned.lower(), cleaned)


async def add_or_update_subscription(
    current_userprofileid: str,
    opponent_userprofileid: str,
    status: str,
) -> list[dict]:
    """Insert or update the subscription between two profiles with a normalised status and return the stored rows."""
    status = normalize_subscription_status(status)
    select_query = """
    SELECT
        subscriptionsid,
        subscriptionstatus
    FROM public.etown_subscriptions
    WHERE
        current_userprofileid = $1
        AND opponent_userprofileid = $2;
    """
    existing_rows = await Database.fetch(
        select_query,
        current_userprofileid,
        opponent_userprofileid,
    )

    if existing_rows:
        subscription_id = existing_rows[0]["subscriptionsid"]
        update_query = """
        UPDATE public.etown_subscriptions
        SET
            subscriptionstatus = $1
        WHERE subscriptionsid = $2
        RETURNING
            subscriptionsid,
            current_userprofileid,
            current_userprofileidname,
            opponent_userprofileid,
            opponent_userprofileidname,
            subscriptionstatus;
        """
        updated_rows = await Database.fetch(update_query, status, subscription_id)
        return [dict(row) for row in updated_rows]

    insert_query = """
    INSERT INTO public.etown_subscriptions (
        subscriptionsid,
        current_userprofileid,
        current_userprofileidname,
        opponent_userprofileid,
        opponent_userprofileidname,
        subscriptionstatus
    )
    VALUES (
        gen_random_uuid()::text,
        $1,
        (SELECT name FROM public.etown_userprofile WHERE userprofileid = $1),
        $2,
        (SELECT name FROM public.etown_userprofile WHERE userprofileid = $2),
        $3
    )
    RETURNING
        subscriptionsid,
        current_userprofileid,
        current_userprofileidname,
        opponent_userprofileid,
        opponent_userprofileidname,
        subscriptionstatus;
    """
    inserted_rows = await Database.fetch(
        insert_query,
        current_userprofileid,
        opponent_userprofileid,
        status,
    )
    return [dict(row) for row in inserted_rows]


async def upload_info_image(
    content: bytes,
    filename: str | None = None,
    content_type: str | None = None,
) -> dict:
    """Upload raw image bytes to the blob banners folder unchanged.

    Args:
        content: Raw image bytes.
        filename: Original filename, used only to recover the extension.
        content_type: Client-supplied MIME type, used when sniffing is inconclusive.

    Returns:
        Dict with url, blob_path, content_type and size_bytes.
    """
    detected_type = detect_mime_type(content)
    if detected_type == "application/octet-stream" and content_type:
        detected_type = content_type

    extension = ""
    if filename and "." in filename:
        extension = filename.rsplit(".", 1)[-1].strip().lower()
    if not extension:
        subtype = detected_type.split("/")[-1] if "/" in detected_type else ""
        extension = "jpg" if subtype == "jpeg" else (subtype or "bin")

    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER or "etowndev")
    blob_name = f"{BANNER_BLOB_FOLDER}/{current_timestamp_prefix()}_{uuid.uuid4().hex}.{extension}"

    blob_path = await upload_blob_bytes(container, blob_name, content, detected_type)

    return {
        "url": to_public_blob_url(blob_path) or "",
        "blob_path": blob_path,
        "content_type": detected_type,
        "size_bytes": len(content),
    }


MEDIA_BLOB_FOLDER = "media"

MAX_MEDIA_BYTES = 100 * 1024 * 1024

_EXTENSION_TYPES: dict = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
    "rtf": "application/rtf",
    "json": "application/json",
    "zip": "application/zip",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "heic": "image/heic",
}

_GENERIC_MEDIA_TYPES = {"", "application/octet-stream", "binary/octet-stream"}

_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

_UNSAFE_EXTENSION_CHARS = re.compile(r"[^a-z0-9]+")


def _media_extension(filename: Optional[str]) -> str:
    """Return a filename's lowercased extension with non-alphanumerics dropped, capped at 12 characters."""
    if not filename or "." not in filename:
        return ""
    extension = filename.rsplit(".", 1)[-1].strip().lower()
    return _UNSAFE_EXTENSION_CHARS.sub("", extension)[:12]


def resolve_media_content_type(
    content: bytes,
    filename: Optional[str] = None,
    declared_type: Optional[str] = None,
) -> str:
    """Return an upload's MIME type from magic bytes, then the declared type, then the extension."""
    sniffed = detect_mime_type(content)
    if sniffed not in _GENERIC_MEDIA_TYPES:
        return sniffed

    declared = (declared_type or "").strip().lower()
    if declared and declared not in _GENERIC_MEDIA_TYPES:
        return declared

    return _EXTENSION_TYPES.get(_media_extension(filename), "application/octet-stream")


def _media_blob_name(filename: Optional[str], content_type: str) -> str:
    """Build a unique media blob name from the sanitised filename stem, a timestamp and a uuid4."""
    extension = _media_extension(filename)
    if not extension:
        subtype = content_type.split("/")[-1] if "/" in content_type else ""
        extension = "jpg" if subtype == "jpeg" else (_UNSAFE_NAME_CHARS.sub("", subtype) or "bin")

    stem = ""
    if filename:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        stem = _UNSAFE_NAME_CHARS.sub("_", stem).strip("._-")[:60]

    unique = f"{current_timestamp_prefix()}_{uuid.uuid4().hex}"
    prefix = f"{stem}_" if stem else ""
    return f"{MEDIA_BLOB_FOLDER}/{prefix}{unique}.{extension}"


async def upload_media_file(
    content: bytes,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> dict:
    """Upload any file's raw bytes to the blob media folder unchanged.

    Args:
        content: Raw file bytes.
        filename: Original filename, used for the extension and the blob name.
        content_type: The multipart part's Content-Type, used when sniffing is inconclusive.

    Returns:
        Dict with url, blob_path, content_type and size_bytes.

    Raises:
        RuntimeError: When AZURE_BLOB_CONTAINER is not configured.
    """
    resolved_type = resolve_media_content_type(content, filename, content_type)

    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container:
        logger.error(
            "INFO_UPLOAD |BLOB_CONTAINER_MISSING | AZURE_BLOB_CONTAINER is not set, so no "
            "upload can be stored | filename=%s", filename or "(unnamed)",
        )
        raise RuntimeError("AZURE_BLOB_CONTAINER is not configured")

    blob_name = _media_blob_name(filename, resolved_type)
    blob_path = await upload_blob_bytes(container, blob_name, content, resolved_type)

    return {
        "url": to_public_blob_url(blob_path) or "",
        "blob_path": blob_path,
        "content_type": resolved_type,
        "size_bytes": len(content),
    }
